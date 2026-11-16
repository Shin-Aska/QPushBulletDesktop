#!/usr/bin/env python3
"""CI-only deterministic-layout AppImage builder; standard library helpers are testable.

This intentionally does not install dependencies. The workflow supplies an isolated,
hash-locked build environment and a clean ephemeral runner.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'runtime-manifest.json'
IDENTITY = 'runtime-id'
FORBIDDEN_PARTS = {'.git', '.venv', '.build-venv', '.test-venv', '.runtime', '.aws',
                   '.agents', '.codex', '__pycache__', '.pytest_cache', 'build', 'dist'}


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def safe_source_path(path):
    parts = Path(path).parts
    name = parts[-1]
    return (not Path(path).is_absolute() and '..' not in parts
            and not FORBIDDEN_PARTS.intersection(parts)
            and not name.startswith(('.env', '.pbdesk-', '.credentials-', '.history-record-', 'pbdesk-recovery'))
            and name != 'service-credentials.json'
            and not any(s in name for s in ('.sqlite3', '.sqlite-wal', '.sqlite-shm'))
            and not name.endswith(('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite-journal'))
            and not name.endswith(('.pyc', '.pyo', '.log')))


def source_archive(root, destination, *, epoch):
    """Archive actual build inputs, including dirty tracked/new nonignored files.

    Never archive the whole checkout directory: private profiles may live beside it.
    The manifest records exact bytes, rather than claiming HEAD equals a dirty tree.
    """
    result = run(['git', '-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                 stdout=subprocess.PIPE)
    names = sorted(set(os.fsdecode(n) for n in result.stdout.split(b'\0') if n))
    entries = {}
    with tarfile.open(destination, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for name in names:
            if not safe_source_path(name):
                continue
            path = root / name
            if not path.exists() and not path.is_symlink():
                continue  # Deletions in the selected working tree are real deletions.
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Unexpected non-regular source input: {name}')
            entry = archive.gettarinfo(str(path), arcname='pbdesk-source/' + name)
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ''
            entry.mtime = epoch
            entry.mode = 0o755 if info.st_mode & 0o111 else 0o644
            with path.open('rb') as stream:
                archive.addfile(entry, stream)
            entries[name] = {'sha256': sha256(path), 'mode': entry.mode}
    return entries


def runtime_manifest(root):
    """Normalize permissions, remove empty directories, seal the exact runtime tree."""
    root = Path(root)
    for name in (MANIFEST, IDENTITY):
        (root / name).unlink(missing_ok=True)
    files = {}
    for directory, dirs, names in os.walk(root, topdown=False, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if not safe_source_path(relative):
                raise ValueError(f'Private or generated state in frozen runtime: {relative}')
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if Path(target).is_absolute():
                    raise ValueError(f'Absolute runtime symlink: {relative}')
                try:
                    path.resolve(strict=True).relative_to(root.resolve())
                except (ValueError, OSError, RuntimeError):
                    raise ValueError(f'Broken/escaping runtime symlink: {relative}') from None
                files[relative] = {'type': 'symlink', 'target': target}
            elif stat.S_ISREG(info.st_mode):
                mode = 0o755 if info.st_mode & 0o111 else 0o644
                path.chmod(mode)
                files[relative] = {'type': 'file', 'sha256': sha256(path), 'mode': mode}
            elif stat.S_ISDIR(info.st_mode):
                if not any(path.iterdir()):
                    path.rmdir()
                else:
                    path.chmod(0o755)
            else:
                raise ValueError(f'Unexpected runtime file type: {relative}')
    root.chmod(0o755)
    if files.get('pbdesk', {}).get('mode') != 0o755:
        raise ValueError('The frozen pbdesk executable is missing')
    raw = canonical({'version': 1, 'files': files})
    (root / MANIFEST).write_bytes(raw)
    identity = hashlib.sha256(raw).hexdigest()
    (root / IDENTITY).write_text(identity + '\n', encoding='ascii')
    for name in (MANIFEST, IDENTITY):
        (root / name).chmod(0o644)
    return identity


def verified_download(record, destination):
    with urllib.request.urlopen(record['url'], timeout=60) as response, destination.open('wb') as output:
        shutil.copyfileobj(response, output)
    if sha256(destination) != record['sha256']:
        destination.unlink()
        raise ValueError('Official build input SHA256 mismatch: ' + record['url'])


def collect_notices(destination, runtime_names):
    """Copy actual wheel/CPython license texts; explicitly account for source gaps."""
    destination.mkdir(parents=True)
    gaps, distributions = [], []
    for name in runtime_names + ['pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph']:
        distribution = importlib.metadata.distribution(name)
        licenses = []
        for file in distribution.files or []:
            label = str(file).lower()
            if ('.dist-info/licenses/' in label or '.dist-info/license' in label
                    or '.dist-info/copying' in label or '.dist-info/notice' in label):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = destination / 'python-distributions' / distribution.metadata['Name'] / str(file)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    licenses.append(target.relative_to(destination).as_posix())
        if not licenses:
            gaps.append('Missing wheel license text: ' + name)
        distributions.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                              'license_texts': licenses})
    python_licenses = [Path(sys.base_prefix) / 'LICENSE.txt',
                       Path(sys.base_prefix) / 'lib/python3.14/LICENSE.txt']
    found = next((p for p in python_licenses if p.is_file()), None)
    if found:
        shutil.copyfile(found, destination / 'CPython-LICENSE.txt')
    else:
        gaps.append('CPython license text location requires review')
    for name in ('LICENSE', 'PRIVACY.md', 'CHANGELOG.md'):
        shutil.copyfile(ROOT / name, destination / name)
    # URLs/inventories are not substitutes for corresponding-source obligations.
    gaps += ['Complete corresponding-source bundle and build scripts for bundled GPL/LGPL/native components',
             'Native-library provenance and license review (Qt, SQLCipher/OpenSSL, CPython, AppImage runtime and runner libraries)',
             'Supported KDE/Wayland desktop acceptance and encrypted-profile upgrade evidence']
    accounting = {'status': 'candidate-only', 'final_release_ready': False,
                  'blockers': gaps, 'python_distributions': distributions}
    (destination / 'SOURCE-COMPLIANCE.json').write_bytes(canonical(accounting))
    return accounting


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args(argv)
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Packaging builds run only on temporary GitHub Actions runners; no local installation is authorized')
    toolchain = json.loads((ROOT / 'packaging/toolchain.json').read_text())
    if platform.machine() != toolchain['architecture'] or platform.python_version() != toolchain['python']['version']:
        parser.error('Build requires pinned CPython 3.14.4 on Linux x86_64')
    if importlib.metadata.version('pyinstaller') != toolchain['pyinstaller']:
        parser.error('PyInstaller version differs from the reviewed pin')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    prefix = 'pbdesk-' + version
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'build/appimage'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    epoch = int(run(['git', '-C', ROOT, 'log', '-1', '--format=%ct'], stdout=subprocess.PIPE).stdout)
    os.environ['SOURCE_DATE_EPOCH'] = str(epoch)
    run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
         '--distpath', work / 'frozen', '--workpath', work / 'pyinstaller',
         ROOT / 'packaging/pbdesk-appimage.spec'], cwd=ROOT)
    appdir = work / 'AppDir'
    runtime = appdir / 'usr/pbdesk'
    runtime.parent.mkdir(parents=True)
    shutil.copytree(work / 'frozen/pbdesk', runtime, symlinks=True)
    runtime_names = [line.split('==', 1)[0] for line in (ROOT / 'packaging/requirements-runtime.lock').read_text().splitlines()
                     if line and not line.startswith(('#', ' ', '\t'))]
    notices = runtime / '_internal/licenses/distribution'
    compliance = collect_notices(notices, runtime_names)
    runtime_id = runtime_manifest(runtime)
    # Validate builder/bootstrap contract before making an image.
    from pbdesk.packaged import verify_runtime
    assert verify_runtime(runtime) == runtime_id
    shutil.copyfile(ROOT / 'packaging/AppRun', appdir / 'AppRun')
    (appdir / 'AppRun').chmod(0o755)
    shutil.copyfile(ROOT / 'packaging/pbdesk.desktop', appdir / 'pbdesk.desktop')
    icon = ROOT / 'src/pbdesk/desktop/assets/pbdesk.svg'
    shutil.copyfile(icon, appdir / 'pbdesk.svg')
    (appdir / '.DirIcon').symlink_to('pbdesk.svg')
    runtime_binary = work / 'appimage-runtime'
    verified_download(toolchain['appimage_runtime'], runtime_binary)
    squashfs = work / 'filesystem.squashfs'
    run(['mksquashfs', appdir, squashfs, '-noappend', '-all-root', '-comp', 'zstd',
         '-no-xattrs', '-mkfs-time', str(epoch), '-all-time', str(epoch)])
    image = output / (prefix + '-x86_64.AppImage')
    with image.open('wb') as destination:
        for component in (runtime_binary, squashfs):
            with component.open('rb') as source:
                shutil.copyfileobj(source, destination)
    image.chmod(0o755)
    # Type-2 runtime's AppImage magic; the runtime already contains ELF headers.
    with image.open('r+b') as destination:
        destination.seek(8)
        destination.write(b'AI\x02')
    source = output / (prefix + '-source.tar.gz')
    source_files = source_archive(ROOT, source, epoch=epoch)
    license_archive = output / (prefix + '-licenses.tar.gz')
    with tarfile.open(license_archive, 'w:gz') as archive:
        archive.add(notices, arcname='pbdesk-licenses')
    packages = [{'name': d.metadata['Name'], 'version': d.version}
                for d in importlib.metadata.distributions()]
    info = {'version': version, 'candidate': True, 'final_release_ready': False,
            'source_revision': run(['git', '-C', ROOT, 'rev-parse', 'HEAD'], stdout=subprocess.PIPE, text=True).stdout.strip(),
            'source_status': run(['git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=no'], stdout=subprocess.PIPE, text=True).stdout.splitlines(),
            'source_files': source_files, 'runtime_id': runtime_id, 'toolchain': toolchain,
            'python': sys.version, 'platform': platform.platform(),
            'python_distributions': sorted(packages, key=lambda p:p['name'].lower()),
            'runner_packages': run(['dpkg-query', '-W', '-f=${Package}\t${Version}\n'], stdout=subprocess.PIPE, text=True).stdout.splitlines(),
            'squashfs_tools': run(['mksquashfs', '-version'], stdout=subprocess.PIPE, text=True).stdout.splitlines(),
            'source_compliance': compliance,
            'lockfiles': {p: sha256(ROOT / p) for p in ['requirements-build.lock', 'packaging/requirements-runtime.lock', 'packaging/requirements-test.lock']}}
    record = output / (prefix + '-build-info.json')
    record.write_bytes(canonical(info))
    assets = [image, source, license_archive, record]
    (output / (prefix + '-SHA256SUMS')).write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in assets))
    print(json.dumps({'appimage': str(image), 'runtime_id': runtime_id, 'final_release_ready': False}))


if __name__ == '__main__':
    main()

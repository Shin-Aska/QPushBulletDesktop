"""Account for frozen inputs and collect applicable source/notices on CI runners.

Downloads are data only. No downloaded build recipe or source file is executed.
The ledger distinguishes source obligations from permissive provenance limits.
"""
import email.parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def request_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'pbdesk-source-accounting/1.0'}), timeout=90) as stream:
        return json.load(stream)


def download(url, target, expected=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'pbdesk-source-accounting/1.0'}), timeout=90) as stream, target.open('wb') as output:
        shutil.copyfileobj(stream, output)
    actual = digest(target)
    if expected and actual != expected:
        target.unlink()
        raise ValueError('Source/notice SHA256 mismatch: ' + url)
    return {'file': target.name, 'sha256': actual, 'size_bytes': target.stat().st_size, 'url': url}


def notice_name(name):
    base = Path(name).name.lower()
    return (any(word in base for word in ('license', 'copying', 'copyright', 'notice', 'attribution'))
            or base in ('ftl.txt', 'lgpl2.txt', 'lgpl3.txt', 'gpl.txt'))


def archive_notices(archive, destination):
    """Copy regular license/attribution texts without extracting executable source."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            parts = Path(member.name).parts
            if not member.isfile() or '..' in parts or Path(member.name).is_absolute() or not notice_name(member.name):
                continue
            if member.size > 4 * 1024 * 1024:
                raise ValueError('Unexpectedly large license document: ' + member.name)
            target = destination.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as stream, target.open('wb') as output:
                shutil.copyfileobj(stream, output)
            copied.append(target.relative_to(destination).as_posix())
    return copied


def verify_git_archive(path, repository, commit):
    """Check every archived blob against the pinned upstream Git tree, not a tag."""
    tree = request_json(f'https://api.github.com/repos/{repository}/git/trees/{commit}?recursive=1')
    if tree.get('truncated'):
        raise ValueError('Upstream source tree was truncated')
    expected = {item['path']: item for item in tree['tree'] if item['type'] == 'blob'}
    if any(item['type'] == 'commit' for item in tree['tree']):
        raise ValueError('Git submodule source requires explicit separate accounting')
    seen = set()
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            name = '/'.join(member.name.split('/')[1:])
            if not name or member.isdir():
                continue
            entry = expected.get(name)
            if entry is None:
                raise ValueError('Unexpected upstream archive entry: ' + name)
            if member.isfile():
                raw = archive.extractfile(member).read()
            elif member.issym():
                raw = member.linkname.encode()
            else:
                raise ValueError('Unsupported upstream archive entry: ' + name)
            if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != entry['sha']:
                raise ValueError('Pinned Git source blob mismatch: ' + name)
            seen.add(name)
    if seen != set(expected):
        raise ValueError('Pinned upstream source archive is incomplete')


def wheel_index():
    owners, distributions = {}, {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        key = re.sub(r'[-_.]+', '-', name).lower()
        distributions[key] = distribution
        for file in distribution.files or []:
            owners[str(Path(distribution.locate_file(file)).resolve())] = key
    return owners, distributions


def package_owner(path):
    candidates = [str(path)]
    if str(path).startswith('/usr/lib/'):
        candidates.append(str(path).removeprefix('/usr'))
    for candidate in candidates:
        result = subprocess.run(['dpkg-query', '--search', candidate], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            break
    else:
        raise ValueError('No Ubuntu binary package owns frozen input: ' + str(path))
    owner = result.stdout.splitlines()[0].rsplit(': ', 1)[0]
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}', owner],
                            capture_output=True, text=True, check=True)
    package, version, source, source_version = result.stdout.split('\t')
    return {'kind': 'ubuntu', 'package': package, 'version': version,
            'source_package': source or package.split(':')[0], 'source_version': source_version or version}


def native_inventory(runtime, inputs, *, base_prefix=None, package_resolver=package_owner):
    """Map every final ELF through the actual PyInstaller input table to its origin."""
    owners, distributions = wheel_index()
    records = {item['destination']: item for item in inputs['binaries']}
    base = Path(base_prefix or sys.base_prefix).resolve()
    entries = []
    for path in sorted(runtime.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        relative = path.relative_to(runtime).as_posix()
        if relative == 'pbdesk':
            entry = {'kind': 'pyinstaller-bootloader', 'distribution': 'pyinstaller',
                     'version': importlib.metadata.version('pyinstaller')}
            input_hash = None  # EXE appends the application's PYZ; it is not the raw bootloader.
        else:
            destination = relative.removeprefix('_internal/')
            if destination not in records:
                raise ValueError('Frozen ELF lacks Analysis provenance: ' + relative)
            source = Path(records[destination]['source']).resolve()
            key = owners.get(str(source))
            if key:
                distribution = distributions[key]
                entry = {'kind': 'wheel', 'distribution': distribution.metadata['Name'], 'version': distribution.version,
                         'wheel_member': str(source.relative_to(Path(distribution.locate_file('')).resolve()))}
            elif source.is_relative_to(base):
                entry = {'kind': 'cpython', 'version': '.'.join(map(str, sys.version_info[:3])),
                         'archive_member': source.relative_to(base).as_posix()}
            else:
                entry = package_resolver(source)
                entry['installed_path'] = str(source)
            input_hash = digest(source)
        entries.append({'path': relative, 'sha256': digest(path), 'input_sha256': input_hash, 'origin': entry})
    if not entries or not any(item['path'] == 'pbdesk' for item in entries):
        raise ValueError('Frozen ELF inventory is empty or missing its executable')
    return entries


def dsc_checksums(raw):
    # Deb822 parser tolerates the PGP clear-signed envelope; checksum lines are
    # bound to the exact .dsc downloaded over the official Ubuntu publisher API.
    match = re.search(r'^Checksums-Sha256:\s*\n((?:[ \t].*\n)+)', raw, re.MULTILINE)
    if not match:
        raise ValueError('Ubuntu source descriptor lacks SHA256 checksums')
    result = {}
    for line in match.group(1).splitlines():
        sha, size, name = line.split()
        if not re.fullmatch(r'[a-f0-9]{64}', sha) or Path(name).name != name:
            raise ValueError('Unsafe Ubuntu source filename/checksum')
        result[name] = {'sha256': sha, 'size_bytes': int(size)}
    return result


def ubuntu_source(origin, destination):
    query = urllib.parse.urlencode({'ws.op': 'getPublishedSources', 'source_name': origin['source_package'],
                                   'version': origin['source_version'], 'exact_match': 'true'})
    published = request_json('https://api.launchpad.net/1.0/ubuntu/+archive/primary?' + query)
    entries = [entry for entry in published['entries'] if entry['source_package_version'] == origin['source_version']]
    if not entries:
        raise ValueError('Exact Ubuntu patched source is unavailable: ' + str(origin))
    urls = request_json(entries[0]['self_link'] + '?ws.op=sourceFileUrls')
    by_name = {urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit('/', 1)[-1]): url for url in urls}
    dsc_name = next((name for name in by_name if name.endswith('.dsc')), None)
    if not dsc_name:
        raise ValueError('Exact Ubuntu source has no descriptor')
    dsc = download(by_name[dsc_name], destination / dsc_name)
    checksums = dsc_checksums((destination / dsc_name).read_text())
    files = [dsc]
    for name, checksum in checksums.items():
        if name not in by_name:
            raise ValueError('Ubuntu publisher omits source descriptor member: ' + name)
        file = download(by_name[name], destination / name, checksum['sha256'])
        if file['size_bytes'] != checksum['size_bytes']:
            raise ValueError('Ubuntu source descriptor size mismatch')
        files.append(file)
    return {'publisher': entries[0]['self_link'], 'files': files,
            'patches_and_build_recipe': dsc_name, 'source_version': origin['source_version']}


def pypi_source(name, version):
    metadata = request_json(f'https://pypi.org/pypi/{name}/{version}/json')
    source = next((file for file in metadata['urls'] if file['packagetype'] == 'sdist'), None)
    return None if not source else {'url': source['url'], 'sha256': source['digests']['sha256'],
                                    'file': source['filename'], 'size_bytes': source['size']}


def wheel_source_rule(distribution, policy):
    """Fail on new/unclassified source-license obligations instead of guessing."""
    key = re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower()
    terms = (distribution.metadata.get('License-Expression') or distribution.metadata.get('License')
             or '\n'.join(value for value in distribution.metadata.get_all('Classifier', [])
                          if value.startswith('License ::'))).strip()
    copyleft = bool(re.search(r'\b(?:A?GPL|LGPL|MPL)(?:[-v ]|\b)|General Public License|Mozilla Public License', terms))
    rule = policy.get('source_license_rules', {}).get(key)
    override = policy.get('license_metadata_overrides', {}).get(key)
    if rule or override:
        result = dict(rule or override)
        if terms != result['metadata_license']:
            raise ValueError('Reviewed distribution license metadata changed: ' + key)
        if result['kind'] == 'module-archives':
            available = {item['id'] for item in policy['archives'] if item.get('required_source')}
            if distribution.version != result['version'] or not set(result['components']) <= available:
                raise ValueError('Matching module/translation sources are incomplete: ' + key)
        result['observed_license'] = terms
        return result
    if copyleft:
        raise ValueError('Unclassified source-license obligation: ' + key + ': ' + terms)
    if not terms or not re.search(r'MIT|BSD|Apache|PSF|Python Software Foundation|ISC|Unicode|Unlicense|CC0|Zlib', terms, re.IGNORECASE):
        raise ValueError('Unclassified distribution license: ' + key)
    return {'kind': 'notice', 'observed_license': terms}


def copy_wheel_notices(distribution, destination):
    copied = []
    for file in distribution.files or []:
        label = str(file).lower()
        if '.dist-info/' not in label or not notice_name(label):
            continue
        source = Path(distribution.locate_file(file))
        if not source.is_file():
            continue
        target = destination / str(file)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(target.relative_to(destination).as_posix())
    return copied


def cargo_notices(distribution, source_dir, notice_dir):
    components = []
    for file in distribution.files or []:
        if '.dist-info/sboms/' not in str(file) or not str(file).endswith('.json'):
            continue
        sbom = json.loads(Path(distribution.locate_file(file)).read_text())
        for component in sbom.get('components', []):
            purl = component.get('purl', '')
            if not purl.startswith('pkg:cargo/') or 'file://' in purl:
                continue
            expected = next((item['content'] for item in component.get('hashes', []) if item['alg'] == 'SHA-256'), None)
            if not expected:
                raise ValueError('Compiled Rust component lacks source checksum: ' + purl)
            name, version = component['name'], component['version']
            archive = source_dir / f'{name}-{version}.crate'
            if not archive.exists():
                download(f'https://static.crates.io/crates/{name}/{name}-{version}.crate', archive, expected)
            elif digest(archive) != expected:
                raise ValueError('Inconsistent compiled Rust source identity: ' + purl)
            notices = archive_notices(archive, notice_dir / name / version)
            if not notices:
                raise ValueError('Compiled Rust component has no license/copyright notice: ' + purl)
            components.append({'name': name, 'version': version, 'sha256': expected,
                               'source_url': f'https://static.crates.io/crates/{name}/{name}-{version}.crate',
                               'licenses': component.get('licenses', []), 'notices': notices})
    return components


def source_parts(source_root, output, prefix, *, limit=90 * 1024 * 1024):
    """Plain tar parts retain already-compressed original/distfile bytes unchanged."""
    groups, current, size = [], [], 0
    for path in sorted(source_root.rglob('*')):
        if not path.is_file():
            continue
        if current and size + path.stat().st_size > limit:
            groups.append(current)
            current, size = [], 0
        current.append(path)
        size += path.stat().st_size
    if current:
        groups.append(current)
    parts = {}
    for index, paths in enumerate(groups, 1):
        target = output / f'{prefix}-corresponding-source-{index:02d}.tar'
        with tarfile.open(target, 'w', format=tarfile.PAX_FORMAT) as archive:
            for path in paths:
                archive.add(path, arcname=path.relative_to(source_root).as_posix(), recursive=False)
        parts[target.name] = digest(target)
    return parts


def collect(root, runtime, inputs_path, work, output, version, toolchain):
    """Emit source evidence; any unmapped ELF or required-source gap fails the build."""
    policy = json.loads((root / 'packaging/source-policy.json').read_text())
    prefix = 'pbdesk-' + version
    source_root = work / 'corresponding-source'
    source_root.mkdir()
    notice_root = runtime / '_internal/licenses/distribution'
    notice_root.mkdir(parents=True)
    inputs = json.loads(inputs_path.read_text())
    native = native_inventory(runtime, inputs)
    write_json(output / (prefix + '-source-progress.json'), {
        'schema_version': 1, 'status': 'collecting', 'native_elf_files': native,
        'scope': 'Sanitized build-input provenance; no profile/cache or wallet data'})
    owners, distributions = wheel_index()
    records, wheels, ubuntu = [], [], {}
    # All runtime wheels plus any additional distribution actually contributing
    # frozen data/code are recorded by the Analysis table, never guessed from ldd.
    required = {re.sub(r'[-_.]+', '-', line.split('==')[0]).lower() for line in (root / 'packaging/requirements-runtime.lock').read_text().splitlines()
                if line and not line.startswith(('#', ' ', '\t'))}
    required |= {re.sub(r'[-_.]+', '-', item['origin']['distribution']).lower() for item in native
                 if 'distribution' in item['origin']}
    required.add('pyinstaller-hooks-contrib')
    # Include additional wheels whose pure modules/resources Analysis actually
    # incorporated (for example a backend's bundled compatibility helper).
    for kind in ('pure', 'datas'):
        for item in inputs.get(kind, []):
            key = owners.get(str(Path(item['source']).resolve()))
            if key and key != 'pbdesk':
                required.add(key)
    for key in sorted(required):
        distribution = distributions[key]
        name, wheel_version = distribution.metadata['Name'], distribution.version
        notices = copy_wheel_notices(distribution, notice_root / 'python-distributions' / name)
        if not notices:
            raise ValueError('Runtime distribution lacks its actual license text: ' + name)
        rule = wheel_source_rule(distribution, policy)
        source = pypi_source(name, wheel_version)
        source_required = rule['kind'] == 'sdist' or key in {
            re.sub(r'[-_.]+', '-', n).lower() for n in policy['required_wheel_sources']}
        wheel = {'name': name, 'version': wheel_version, 'notices': notices, 'upstream_source': source,
                 'source_archived': source_required or rule['kind'] == 'module-archives',
                 'source_license_decision': rule}
        if source_required:
            if source is None:
                raise ValueError('Required wheel corresponding source unavailable: ' + name)
            path = source_root / 'python' / source['file']
            records.append({'component': name, **download(source['url'], path, source['sha256'])})
            archive_notices(path, notice_root / 'source-notices' / name)
        wheel['compiled_rust_components'] = cargo_notices(distribution, work / 'notice-sources', notice_root / 'rust')
        wheels.append(wheel)
    for item in native:
        origin = item['origin']
        if origin['kind'] != 'ubuntu':
            continue
        package = origin['package'].split(':')[0]
        if package in ubuntu:
            continue
        copyright_path = Path('/usr/share/doc') / package / 'copyright'
        if not copyright_path.is_file():
            raise ValueError('Native Ubuntu library copyright text unavailable: ' + package)
        target = notice_root / 'ubuntu' / package / 'copyright'
        target.parent.mkdir(parents=True)
        shutil.copyfile(copyright_path, target)
        text = copyright_path.read_text(errors='strict')
        common = []
        for name in set(re.findall(r'/usr/share/common-licenses/([A-Za-z0-9_.+-]+)', text)):
            path = Path('/usr/share/common-licenses') / name
            if path.is_file():
                (notice_root / 'common-licenses').mkdir(exist_ok=True)
                shutil.copyfile(path, notice_root / 'common-licenses' / name)
                common.append(name)
        entry = {**origin, 'copyright_sha256': digest(target), 'common_license_texts': common}
        # This conservative package-level selection may archive extra utilities'
        # source as well. The GCC runtime exception is explicitly documented.
        exception = policy['system_source_exceptions'].get(origin['source_package'])
        needs_source = bool(re.search(r'\b(?:GPL|LGPL|MPL)[- \n:]|General Public License|Mozilla Public License', text)) and not exception
        entry['source_required'] = needs_source
        entry['source_exception'] = exception
        if needs_source:
            destination = source_root / 'ubuntu' / (origin['source_package'] + '-' + origin['source_version'])
            destination.mkdir(parents=True, exist_ok=True)
            entry['corresponding_source'] = ubuntu_source(origin, destination)
        ubuntu[package] = entry
    for entry in policy['archives']:
        filename = entry.get('file') or urllib.parse.urlparse(entry['url']).path.rsplit('/', 1)[-1]
        if entry.get('git_commit'):
            filename = entry['id'] + '-' + entry['git_commit'] + '.tar.gz'
        path = source_root / 'upstream' / filename
        record = download(entry['url'], path, entry.get('sha256'))
        if entry.get('git_commit'):
            verify_git_archive(path, entry['git_repository'], entry['git_commit'])
            record['git_commit'] = entry['git_commit']
        record['component'] = entry['id']
        record['version'] = entry['version']
        records.append(record)
        archive_notices(path, notice_root / 'source-notices' / entry['id'])
    for entry in policy['notices']:
        path = notice_root / 'native-notices' / (entry['id'] + '-LICENSE.txt')
        records.append({'component': entry['id'], 'notice_only': True,
                        **download(entry['url'], path, entry['sha256'])})
    python_license = next((path for path in (Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'lib/python3.14/LICENSE.txt') if path.is_file()), None)
    if python_license is None:
        raise ValueError('CPython license text is missing')
    shutil.copyfile(python_license, notice_root / 'CPython-LICENSE.txt')
    for name in ('LICENSE', 'PRIVACY.md', 'CHANGELOG.md'):
        shutil.copyfile(root / name, notice_root / name)
    # Official wheel SBOMs are distribution evidence, including the exact static
    # OpenSSL source hash/build flags. Preserve them beside the actual notices.
    for distribution in distributions.values():
        if re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower() not in required:
            continue
        for file in distribution.files or []:
            if '.dist-info/sboms/' in str(file):
                path = notice_root / 'upstream-sboms' / str(file)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(file), path)
    shutil.copyfile(root / 'packaging/REBUILDING.md', source_root / 'REBUILDING.md')
    shutil.copyfile(root / 'packaging/source-policy.json', source_root / 'source-policy.json')
    parts = source_parts(source_root, output, prefix)
    ledger = {'schema_version': 1, 'scope': 'Applicable corresponding-source, actual notices and usable library replacement/relink instructions',
              'source_revision': subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip(),
              'complete': True, 'blockers': [], 'native_elf_files': native,
              'python_distributions': wheels, 'ubuntu_packages': list(ubuntu.values()),
              'upstream_materials': records, 'source_archives': parts,
              'appimage_runtime': toolchain['appimage_runtime'],
              'provenance_limitations': [policy['runtime_permissive_provenance_limitation']],
              'rebuilding_instructions_sha256': digest(root / 'packaging/REBUILDING.md')}
    path = output / (prefix + '-corresponding-source.json')
    write_json(path, ledger)
    write_json(notice_root / 'SOURCE-COMPLIANCE.json', {'scope': ledger['scope'], 'corresponding_source_complete': True,
               'source_manifest_sha256': digest(path), 'source_archives': parts,
               'provenance_limitations': ledger['provenance_limitations'], 'blockers': []})
    (output / (prefix + '-source-progress.json')).unlink()
    return {'complete': True, 'blockers': [], 'manifest': path.name, 'manifest_sha256': digest(path),
            'source_archives': parts, 'scope': ledger['scope'], 'provenance_limitations': ledger['provenance_limitations']}

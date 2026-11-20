"""Small frozen entry point and verified persistent AppImage runtime cache.

The cache deliberately has no garbage collector: another desktop, core, or web
lease may still be using an older runtime after the AppImage mount disappears.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile

from pbdesk.runtime import MODULES, child_environment

MANIFEST = 'runtime-manifest.json'
IDENTITY = 'runtime-id'


class PackagedRuntimeError(RuntimeError):
    """Error raised when packaged runtime verification or setup fails."""
    pass


def _fail(message):
    """Raise PackagedRuntimeError with the given message.

    :param message: Human-readable error message describing the runtime failure.
    :type message: str
    :raises PackagedRuntimeError: Always raised with ``message``.
    """
    raise PackagedRuntimeError(message)


def _private_directory(path):
    """Create and verify an owned private directory with 0700 permissions.

    :param path: Directory path to create and validate.
    :type path: pathlib.Path
    :raises PackagedRuntimeError: If ``path`` is not a regular directory owned by the user with ``0700`` mode.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        _fail(f'Runtime cache must be an owned private directory (0700): {path}')


def _regular(path, *, private=False):
    """Validate that a path is a safe regular file and return its stat info.

    :param path: File path to inspect via ``lstat``.
    :type path: pathlib.Path
    :param private: Whether to require current-user ownership and single hard link.
    :type private: bool
    :return: Stat result for the verified regular file.
    :rtype: os.stat_result
    :raises PackagedRuntimeError: If the path fails file type, ownership, permission, or link-count checks.
    """
    info = path.lstat()
    owners = {os.getuid()} if private else {os.getuid(), 0}
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners
            or info.st_mode & 0o6022 or (private and info.st_nlink != 1)):
        _fail(f'Unsafe runtime file: {path}')
    return info


def _manifest(root, *, private=False):
    """Load and validate the runtime manifest and its SHA-256 identity.

    :param root: Root directory containing ``runtime-manifest.json`` and ``runtime-id``.
    :type root: pathlib.Path
    :param private: Whether manifest files must satisfy strict private cache ownership rules.
    :type private: bool
    :return: Tuple of hex SHA-256 runtime identity and validated manifest file entries.
    :rtype: tuple[str, dict[str, dict[str, Any]]]
    :raises PackagedRuntimeError: If the manifest or identity file fails verification or schema validation.
    """
    for name in (MANIFEST, IDENTITY):
        _regular(root / name, private=private)
    raw = (root / MANIFEST).read_bytes()
    identity = (root / IDENTITY).read_text('ascii').strip()
    if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity) or hashlib.sha256(raw).hexdigest() != identity:
        _fail('Runtime manifest identity does not match; obtain a complete AppImage')
    try:
        value = json.loads(raw)
        if set(value) != {'version', 'files'} or type(value['version']) is not int or value['version'] != 1 or not isinstance(value['files'], dict):
            raise ValueError()
        files = value['files']
        if 'pbdesk' not in files or files['pbdesk'].get('type') != 'file' or files['pbdesk'].get('mode') != 0o755:
            raise ValueError()
        for name, entry in files.items():
            path = PurePosixPath(name)
            if (not isinstance(name, str) or not name or path.is_absolute() or str(path) != name
                    or any(part in ('', '.', '..') for part in name.split('/')) or '\\' in name
                    or name in (MANIFEST, IDENTITY) or not isinstance(entry, dict)):
                raise ValueError()
            if entry.get('type') == 'file':
                if set(entry) != {'type', 'sha256', 'mode'} or type(entry['mode']) is not int or entry['mode'] not in (0o644, 0o755):
                    raise ValueError()
                digest = entry['sha256']
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError()
            elif entry.get('type') == 'symlink':
                if set(entry) != {'type', 'target'} or not isinstance(entry['target'], str) or not entry['target'] or PurePosixPath(entry['target']).is_absolute():
                    raise ValueError()
            else:
                raise ValueError()
        if any('/'.join(PurePosixPath(name).parts[:i]) in files for name in files for i in range(1, len(PurePosixPath(name).parts))):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        _fail('Runtime manifest is invalid; obtain a complete AppImage')
    return identity, files


def verify_runtime(root, *, private=False):
    """Verify all entries, including symlinks, without traversing directory links.

    :param root: Runtime bundle directory to verify.
    :type root: pathlib.Path | str
    :param private: Whether to enforce private user-owned ``0700``/``0600`` cache constraints.
    :type private: bool
    :return: Verified hex SHA-256 runtime identity string.
    :rtype: str
    :raises PackagedRuntimeError: If any directory, file, symlink, permission, or digest check fails.
    """
    root = Path(root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid not in ({os.getuid()} if private else {0, os.getuid()}) or root_info.st_mode & 0o6022:
        _fail(f'Unsafe runtime directory: {root}')
    if private and stat.S_IMODE(root_info.st_mode) != 0o700:
        _fail(f'Runtime cache must be private (0700): {root}')
    identity, files = _manifest(root, private=private)
    expected_dirs = {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != '.'}
    seen_files, seen_dirs = set(), set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs) + names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid not in ({os.getuid()} if private else {0, os.getuid()}) or info.st_mode & 0o6022 or (private and stat.S_IMODE(info.st_mode) != 0o700):
                    _fail(f'Unsafe runtime directory: {path}')
                seen_dirs.add(relative)
                continue
            if relative in (MANIFEST, IDENTITY):
                continue
            seen_files.add(relative)
            entry = files.get(relative)
            if entry is None:
                _fail(f'Unexpected runtime entry: {relative}')
            if entry['type'] == 'symlink':
                if not stat.S_ISLNK(info.st_mode) or info.st_uid not in ({os.getuid()} if private else {0, os.getuid()}) or os.readlink(path) != entry['target']:
                    _fail(f'Invalid runtime symlink: {relative}')
                try:
                    target = path.resolve(strict=True)
                    target.relative_to(root.resolve())
                except (ValueError, OSError, RuntimeError):
                    _fail(f'Runtime symlink escapes its bundle or is broken: {relative}')
            else:
                _regular(path, private=private)
                if stat.S_IMODE(info.st_mode) != entry['mode']:
                    _fail(f'Invalid runtime permissions: {relative}')
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                        _fail(f'Runtime content verification failed: {relative}')
    if seen_files != set(files) or seen_dirs != expected_dirs:
        _fail('Runtime is incomplete or contains unexpected directories')
    return identity


def cache_runtime(source, cache_home=None):
    """Verify and cache the packaged runtime in a private user directory.

    :param source: Source runtime directory containing the packaged application bundle.
    :type source: pathlib.Path | str
    :param cache_home: Optional XDG cache base directory override.
    :type cache_home: pathlib.Path | str | None
    :return: Path to the verified cached runtime directory.
    :rtype: pathlib.Path
    :raises PackagedRuntimeError: If verification, lock validation, or cache staging fails.
    """
    source = Path(source)
    identity = verify_runtime(source)
    base = Path(cache_home or os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'pbdesk'
    # The XDG parent can be shared with other applications; our own roots cannot.
    _private_directory(base)
    root = base / 'runtimes'
    _private_directory(root)
    fd = os.open(root / 'runtime.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            _fail('Runtime cache lock must be an owned private regular file (0600)')
        fcntl.flock(fd, fcntl.LOCK_EX)
        destination = root / identity
        if os.path.lexists(destination):
            if verify_runtime(destination, private=True) != identity:
                _fail('Cached runtime identity differs from its directory')
            return destination
        staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=root))
        try:
            shutil.copytree(source, staging, symlinks=True, dirs_exist_ok=True)
            for directory, _, _ in os.walk(staging, followlinks=False):
                os.chmod(directory, 0o700)
            if verify_runtime(staging, private=True) != identity:
                _fail('Runtime changed while staging')
            os.rename(staging, destination)
            return destination
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    finally:
        os.close(fd)


def require_writable_application_directory():
    """Ensure the application directory exists and is writable.

    :raises PackagedRuntimeError: If the application directory cannot be created securely or written to.
    """
    from pbdesk.config import application_directory
    root = application_directory()
    try:
        if os.environ.get('PBDESK_APPLICATION_DIR'):
            # Match source initialization for a fresh explicit history location.
            # Create each missing ancestor privately without altering existing dirs.
            missing = []
            ancestor = root
            while not ancestor.exists():
                missing.append(ancestor)
                ancestor = ancestor.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700, exist_ok=True)
                info = directory.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise OSError('new application directories must be owned private directories (0700)')
        if not root.is_dir() or not root.stat().st_mode & 0o222:
            raise OSError('directory is not writable')
        fd, probe = tempfile.mkstemp(prefix='.pbdesk-write-check-', dir=root)
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        _fail(f'pbdesk needs a writable application directory for encrypted history: {root}. Move the AppImage to a writable folder or set PBDESK_APPLICATION_DIR. ({exc})')


def _dispatch(arguments):
    """Dispatch packaged CLI flags or internal module invocations.

    :param arguments: Command-line arguments excluding the executable name.
    :type arguments: list[str]
    :return: Return value from the dispatched entry point, if any.
    :rtype: Any
    :raises PackagedRuntimeError: If an unsupported internal module or option is requested.
    """
    if arguments[:1] == ['--pbdesk-run-module']:
        if len(arguments) < 2 or arguments[1] not in MODULES:
            _fail('Unsupported internal pbdesk process entry point')
        module, rest = arguments[1], arguments[2:]
        sys.argv = [module, *rest]
        if module == 'pbdesk.cli':
            from pbdesk.cli import core_main
            return core_main(rest)
        if module == 'pbdesk.web':
            from pbdesk.web.app import main
            return main()
        from pbdesk.services.core_settings import restart_helper
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--restart-helper', required=True)
        return restart_helper(parser.parse_args(rest).restart_helper)
    from pbdesk import cli
    entries = {'--core': cli.core_main, '--web': cli.web_main, '--mcp': cli.mcp_main,
               '--admin': cli.admin_main, '--diagnostics': cli.diagnostics_main}
    if arguments and arguments[0] in entries:
        entry, rest = entries[arguments[0]], arguments[1:]
        sys.argv = [sys.executable, *rest]
        return entry()
    if arguments and arguments[0].startswith('--pbdesk-'):
        _fail('Unsupported internal pbdesk option')
    sys.argv = [sys.executable, *arguments]
    return cli.desktop_main()


def main(argv=None):
    """Run the packaged pbdesk bootstrapper and command dispatcher.

    :param argv: Optional argument sequence to dispatch instead of ``sys.argv[1:]``.
    :type argv: Sequence[str] | None
    :return: Process exit status or return value from the dispatched entry point.
    :rtype: int | None
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        require_writable_application_directory()
        if arguments[:1] == ['--pbdesk-bootstrap']:
            cached = cache_runtime(Path(sys.executable).resolve().parent)
            executable = str(cached / 'pbdesk')
            os.execve(executable, [executable, *arguments[1:]], child_environment())
            return 1  # execve never returns
        return _dispatch(arguments)
    except (PackagedRuntimeError, OSError, ValueError) as exc:
        print(f'pbdesk: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

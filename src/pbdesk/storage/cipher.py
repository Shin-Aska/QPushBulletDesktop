"""SQLCipher connections. The key never appears in a SQLAlchemy URL or error."""
import os
import stat
from pathlib import Path


class CipherUnavailable(RuntimeError):
    """Error raised when SQLCipher history encryption is unavailable or fails verification."""
    pass


SIDECARS = ('-wal', '-shm', '-journal')


def present(path):
    """Lexical presence includes broken links, which must never mean 'new'.

    :param path: Filesystem path to test for existence or a symbolic link entry.
    :type path: str | os.PathLike[str]
    :return: ``True`` if the path exists or is a symbolic link, otherwise ``False``.
    :rtype: bool
    """
    path = Path(path)
    return path.exists() or path.is_symlink()


def validate_file(path, *, required=False):
    """Inspect the original directory entry without following links or opening it.

    :param path: Filesystem path to inspect via ``lstat``.
    :type path: str | os.PathLike[str]
    :param required: Whether a missing file raises :class:`CipherUnavailable` instead of returning ``False``.
    :type required: bool
    :return: ``True`` if the path is a user-owned regular file, or ``False`` when absent and ``required`` is ``False``.
    :rtype: bool
    :raises CipherUnavailable: If ``required`` is ``True`` and the file is missing, or if the entry is not a user-owned regular file.
    """
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        if required:
            raise CipherUnavailable('A required history file is missing.') from None
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise CipherUnavailable('History files must be regular files owned by the current user.')
    return True


def create_private_file(path):
    """Reserve a genuinely absent destination without following or replacing it.

    :param path: Destination file path to create exclusively with ``0600`` permissions.
    :type path: str | os.PathLike[str]
    """
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)


def driver():
    """Import and return the sqlcipher3 DB-API module.

    :return: Imported ``sqlcipher3.dbapi2`` DB-API module.
    :rtype: types.ModuleType
    :raises CipherUnavailable: If the ``sqlcipher3`` driver cannot be imported.
    """
    try:
        from sqlcipher3 import dbapi2
        return dbapi2
    except ImportError:
        raise CipherUnavailable('Encrypted history requires the SQLCipher driver.') from None


def connect(path, key, *, readonly=False):
    """Open and verify a SQLCipher database connection using the 32-byte key.

    :param path: Path to the SQLCipher database file.
    :type path: str | os.PathLike[str]
    :param key: 32-byte symmetric encryption key passed to ``PRAGMA key``.
    :type key: bytes
    :param readonly: Whether to open the database in read-only URI mode without enabling WAL.
    :type readonly: bool
    :return: Unlocked and verified SQLCipher database connection.
    :rtype: sqlcipher3.dbapi2.Connection
    :raises CipherUnavailable: If ``key`` is invalid, the driver lacks SQLCipher support, or unlocking fails.
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise CipherUnavailable('A verified history encryption key is required.')
    dbapi = driver()
    path = Path(path)
    exists = validate_file(path)
    for suffix in SIDECARS:
        validate_file(Path(str(path) + suffix))
    if not exists and not readonly:
        create_private_file(path)
    connection = None
    try:
        target = Path(path).resolve().as_uri() + '?mode=ro' if readonly else str(path)
        connection = dbapi.connect(target, uri=readonly, check_same_thread=False, timeout=5)
        # This must precede *all* SQL, including dialect initialization pragmas.
        connection.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
        version = connection.execute('PRAGMA cipher_version').fetchone()
        if not version or not version[0]:
            raise CipherUnavailable('The installed driver does not provide SQLCipher.')
        connection.execute('PRAGMA cipher_memory_security=ON')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')
        if not readonly:
            connection.execute('PRAGMA journal_mode=WAL')
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise CipherUnavailable('Encrypted history could not be unlocked or verified.') from None


def sync_file(path):
    """Flush a file's data and metadata to disk without following symlinks.

    :param path: Regular file path to flush with ``os.fsync``.
    :type path: str | os.PathLike[str]
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_directory(path):
    """Flush directory entries to disk using fsync.

    :param path: Directory path to flush with ``os.fsync``.
    :type path: str | os.PathLike[str]
    """
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

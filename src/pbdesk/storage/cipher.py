"""SQLCipher connections. The key never appears in a SQLAlchemy URL or error."""
import os
import stat
from pathlib import Path


class CipherUnavailable(RuntimeError):
    pass


SIDECARS = ('-wal', '-shm', '-journal')


def present(path):
    """Lexical presence includes broken links, which must never mean 'new'."""
    path = Path(path)
    return path.exists() or path.is_symlink()


def validate_file(path, *, required=False):
    """Inspect the original directory entry without following links or opening it."""
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
    """Reserve a genuinely absent destination without following or replacing it."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)


def driver():
    try:
        from sqlcipher3 import dbapi2
        return dbapi2
    except ImportError:
        raise CipherUnavailable('Encrypted history requires the SQLCipher driver.') from None


def connect(path, key, *, readonly=False):
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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

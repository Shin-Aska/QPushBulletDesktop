"""Read an explicitly selected KWallet password through its session D-Bus API.

Jeepney is pure Python; this adapter requires neither Qt nor dbus-python.
It never creates wallets or returns secrets in error messages. Entry creation is
explicit, refuses overwrites, and verifies the newly written password locally.
"""
import time
import os
import fcntl
import tempfile
import stat

from jeepney import DBusAddress, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection
from jeepney.wrappers import unwrap_msg


class KWalletUnavailable(RuntimeError):
    """Error raised when KWallet is inaccessible, locked, or rejects an entry operation."""
    pass


def _password(folder, entry, wallet=None, new_password=None, provider=False, interactive=False, database=False, authorize=False):
    """Read, authorize, or atomically create a KWallet password entry via D-Bus.

    :param folder: KWallet folder name containing the target entry.
    :type folder: str
    :param entry: KWallet entry key name within ``folder``.
    :type entry: str
    :param wallet: Explicit wallet name, or ``None`` to use KWallet's ``networkWallet``.
    :type wallet: str | None
    :param new_password: Password string to store when creating a new entry, or ``None`` to read.
    :type new_password: str | None
    :param provider: Whether creating a Pushbullet provider credential entry.
    :type provider: bool
    :param interactive: Whether interactive wallet unlocking prompts and longer timeouts are permitted.
    :type interactive: bool
    :param database: Whether operating on a dedicated encrypted history database key entry.
    :type database: bool
    :param authorize: Whether to open and return only the selected wallet name without reading entries.
    :type authorize: bool
    :return: Retrieved password string, selected wallet name when ``authorize`` is true, or ``None`` if absent.
    :rtype: str | None
    :raises KWalletUnavailable: If KWallet is unavailable, locked, rejects access, or violates creation rules.
    """
    deadline = time.monotonic() + (60 if interactive else 8)

    def call(bus, address, method, signature=None, body=(), timeout=2):
        """Send a bounded D-Bus method call and unwrap its single reply value.

        :param bus: Active blocking D-Bus connection.
        :type bus: jeepney.io.blocking.DBusConnection
        :param address: Target D-Bus object address.
        :type address: jeepney.DBusAddress
        :param method: D-Bus method name to invoke.
        :type method: str
        :param signature: Optional D-Bus type signature string for ``body``.
        :type signature: str | None
        :param body: Tuple of arguments marshaled into the D-Bus call.
        :type body: tuple[Any, ...]
        :param timeout: Per-call timeout cap in seconds.
        :type timeout: float
        :return: Single unwrapped return value from the D-Bus reply.
        :rtype: Any
        :raises TimeoutError: If the overall KWallet deadline has already elapsed.
        :raises ValueError: If the D-Bus reply does not contain exactly one value.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        reply = bus.send_and_get_reply(
            new_method_call(address, method, signature, body),
            timeout=min(timeout, remaining),
        )
        values = unwrap_msg(reply)
        if len(values) != 1:
            raise ValueError("Unexpected KWallet reply")
        return values[0]

    try:
        with open_dbus_connection(auth_timeout=1) as bus:
            names = call(bus, message_bus, "ListNames")
            service = next((name for name in ("org.kde.kwalletd6", "org.kde.kwalletd5") if name in names), None)
            if service is None:
                raise KWalletUnavailable()
            address = DBusAddress(
                "/modules/" + service.rsplit(".", 1)[-1],
                bus_name=service, interface="org.kde.KWallet",
            )
            selected = wallet or call(bus, address, "networkWallet")
            if not isinstance(selected, str) or selected not in call(bus, address, "wallets"):
                raise KWalletUnavailable()
            if database and not interactive and not call(bus, address, 'isOpen', 's', (selected,)):
                raise KWalletUnavailable()
            handle = call(bus, address, "open", "sxs", (selected, 0, "pbdesk"), timeout=45 if interactive else 5)
            if type(handle) is not int or handle < 0:
                raise KWalletUnavailable()
            try:
                if authorize:
                    return selected
                if not call(bus, address, "hasFolder", "iss", (handle, folder, "pbdesk")):
                    if (provider or database) and new_password is not None:
                        if call(bus, address, 'createFolder', 'iss', (handle, folder, 'pbdesk')) is not True:
                            raise KWalletUnavailable()
                    elif new_password is not None:
                        raise KWalletUnavailable()
                    else:
                        return None
                exists = call(bus, address, "hasEntry", "isss", (handle, folder, entry, "pbdesk"))
                if new_password is not None:
                    if exists or entry in {'e2e-password', 'ai-openai-api-key'} or (entry == 'api-key' and not provider):
                        raise KWalletUnavailable()
                    result = call(bus, address, "writePassword", "issss", (handle, folder, entry, new_password, "pbdesk"))
                    if result != 0:
                        raise KWalletUnavailable()
                elif not exists:
                    return None
                password = call(bus, address, "readPassword", "isss", (handle, folder, entry, "pbdesk"))
                if not isinstance(password, str):
                    raise KWalletUnavailable()
                if new_password is not None and password != new_password:
                    raise KWalletUnavailable()
                return password or None
            finally:
                try:
                    # Release only this application's handle; never force-lock a wallet.
                    call(bus, address, "close", "ibs", (handle, False, "pbdesk"))
                except Exception:
                    pass
    except Exception:
        raise KWalletUnavailable("KWallet is unavailable or locked; unlock it and allow pbdesk access") from None


def read_password(folder, entry, wallet=None, *, interactive=False):
    """Read an existing password entry from the specified KWallet folder.

    :param folder: KWallet folder name to read from.
    :type folder: str
    :param entry: KWallet entry key name within ``folder``.
    :type entry: str
    :param wallet: Explicit wallet name, or ``None`` to use the default network wallet.
    :type wallet: str | None
    :param interactive: Whether interactive wallet unlocking prompts are allowed.
    :type interactive: bool
    :return: Stored password string, or ``None`` if the folder or entry does not exist.
    :rtype: str | None
    """
    return _password(folder, entry, wallet, interactive=interactive)


def write_new_password(folder, entry, password, wallet=None):
    """Explicit user creation only; no overwriting shared or existing entries.

    :param folder: KWallet folder name in which to create the entry.
    :type folder: str
    :param entry: New KWallet entry key name.
    :type entry: str
    :param password: Non-empty password secret to persist.
    :type password: str
    :param wallet: Explicit wallet name, or ``None`` to use the default network wallet.
    :type wallet: str | None
    :raises KWalletUnavailable: If ``password`` is empty/invalid, ``entry`` is reserved, or creation fails.
    """
    if not isinstance(password,str) or not password or entry in {'api-key','e2e-password'}:
        raise KWalletUnavailable('Choose a new device-specific entry')
    _create_password(folder, entry, password, wallet)


def write_provider_password(folder, entry, password, wallet=None):
    """Explicit first-run token creation; never replace an existing entry.

    :param folder: KWallet folder name in which to store the provider token.
    :type folder: str
    :param entry: Dedicated provider entry key name.
    :type entry: str
    :param password: Non-empty Pushbullet access token to persist.
    :type password: str
    :param wallet: Explicit wallet name, or ``None`` to use the default network wallet.
    :type wallet: str | None
    :raises KWalletUnavailable: If ``password`` is empty/invalid, ``entry`` is reserved, or creation fails.
    """
    if not isinstance(password, str) or not password or entry in {'e2e-password', 'ai-openai-api-key'}:
        raise KWalletUnavailable('Choose a dedicated provider entry')
    _create_password(folder, entry, password, wallet, provider=True)


def _create_password(folder, entry, password, wallet, provider=False, database=False):
    """Create and verify a new KWallet password entry under an exclusive lock.

    :param folder: KWallet folder name in which to create the entry.
    :type folder: str
    :param entry: KWallet entry key name to create.
    :type entry: str
    :param password: Secret string to write and read back for verification.
    :type password: str
    :param wallet: Explicit wallet name, or ``None`` to use the default network wallet.
    :type wallet: str | None
    :param provider: Whether creating a Pushbullet provider credential entry.
    :type provider: bool
    :param database: Whether creating a dedicated encrypted history database key entry.
    :type database: bool
    :raises KWalletUnavailable: If the creation lock file is unsafe or KWallet rejects creation.
    """
    # Serialize the hasEntry/write/read-back sequence across local pbdesk cores
    # and concurrent API requests. Never overwrite another creator's new entry.
    path=os.path.join(tempfile.gettempdir(),f'pbdesk-kwallet-create-{os.getuid()}.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
            raise KWalletUnavailable('KWallet entry creation lock is unavailable')
        fcntl.flock(fd,fcntl.LOCK_EX)
        _password(folder,entry,wallet,new_password=password,provider=provider,interactive=provider or database,database=database)
    finally:
        os.close(fd)


HISTORY_FOLDER = 'pbdesk-history'


def authorize_database_wallet(wallet=None):
    """Authorize an existing wallet before the caller generates a database key.

    :param wallet: Explicit wallet name, or ``None`` to authorize the default network wallet.
    :type wallet: str | None
    :return: Name of the authorized KWallet wallet.
    :rtype: str
    """
    return _password(HISTORY_FOLDER, '', wallet, interactive=True, database=True, authorize=True)


def _history_entry(identity):
    """Validate a UUID identity and return its KWallet history entry name.

    :param identity: Canonical UUID string identifying the history database key.
    :type identity: str
    :return: KWallet entry name prefixed with ``history-``.
    :rtype: str
    :raises KWalletUnavailable: If ``identity`` does not match canonical UUID formatting.
    """
    import uuid
    if str(uuid.UUID(identity)) != identity:
        raise KWalletUnavailable('Invalid history identity')
    return 'history-' + identity


def write_database_key(identity, key, wallet):
    """A fresh dedicated entry only; provider/AI/e2e entries cannot be selected.

    :param identity: Canonical UUID string identifying the new history key entry.
    :type identity: str
    :param key: 32-byte encryption key to store as lowercase hexadecimal.
    :type key: bytes
    :param wallet: Target existing KWallet wallet name.
    :type wallet: str
    :raises KWalletUnavailable: If ``key`` is not 32 bytes, ``wallet`` is empty, or creation fails.
    """
    if not isinstance(key, bytes) or len(key) != 32 or not wallet:
        raise KWalletUnavailable('A dedicated history key and existing wallet are required')
    _create_password(HISTORY_FOLDER, _history_entry(identity), key.hex(), wallet, database=True)


def _existing_wallets():
    """Return the list of existing KWallet wallet names from the session D-Bus.

    :return: List of available KWallet wallet names.
    :rtype: list[str]
    :raises KWalletUnavailable: If the KWallet D-Bus service is unreachable or returns invalid data.
    """
    try:
        with open_dbus_connection(auth_timeout=1) as bus:
            names = unwrap_msg(bus.send_and_get_reply(new_method_call(message_bus, 'ListNames'), timeout=2))[0]
            service = next(name for name in ('org.kde.kwalletd6', 'org.kde.kwalletd5') if name in names)
            address = DBusAddress('/modules/' + service.rsplit('.', 1)[-1], bus_name=service, interface='org.kde.KWallet')
            wallets = unwrap_msg(bus.send_and_get_reply(new_method_call(address, 'wallets'), timeout=2))[0]
            if not isinstance(wallets, list) or any(not isinstance(value, str) for value in wallets):
                raise ValueError()
            return wallets
    except Exception:
        raise KWalletUnavailable('KWallet is unavailable or locked; unlock it and allow pbdesk access') from None


def read_database_key(identity, *, interactive=False):
    """Find the opaque dedicated entry in existing wallets; never creates one.

    :param identity: Canonical UUID string identifying the history key entry.
    :type identity: str
    :param interactive: Whether interactive wallet unlocking prompts are permitted.
    :type interactive: bool
    :return: 32-byte raw history encryption key.
    :rtype: bytes
    :raises KWalletUnavailable: If the key is missing, duplicated, malformed, or KWallet is locked.
    """
    import re
    entry = _history_entry(identity)
    found = []
    unavailable = False
    for wallet in _existing_wallets():
        try:
            value = _password(HISTORY_FOLDER, entry, wallet, interactive=interactive, database=True)
        except KWalletUnavailable:
            unavailable = True
            continue
        if value is not None:
            if not re.fullmatch('[0-9a-f]{64}', value):
                raise KWalletUnavailable('The history key entry is invalid')
            found.append(bytes.fromhex(value))
    if len(found) != 1:
        raise KWalletUnavailable('The history key is missing or KWallet is locked; use your recovery key if needed')
    return found[0]

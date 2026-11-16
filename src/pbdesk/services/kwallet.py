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
    pass


def _password(folder, entry, wallet=None, new_password=None, provider=False, interactive=False):
    deadline = time.monotonic() + (60 if interactive else 8)

    def call(bus, address, method, signature=None, body=(), timeout=2):
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
            handle = call(bus, address, "open", "sxs", (selected, 0, "pbdesk"), timeout=45 if interactive else 5)
            if type(handle) is not int or handle < 0:
                raise KWalletUnavailable()
            try:
                if not call(bus, address, "hasFolder", "iss", (handle, folder, "pbdesk")):
                    if provider and new_password is not None:
                        if call(bus, address, 'createFolder', 'iss', (handle, folder, 'pbdesk')) is not True:
                            raise KWalletUnavailable()
                    elif new_password is not None:
                        raise KWalletUnavailable()
                    else:
                        return None
                exists = call(bus, address, "hasEntry", "isss", (handle, folder, entry, "pbdesk"))
                if new_password is not None:
                    if exists or entry == 'e2e-password' or (entry == 'api-key' and not provider):
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
    return _password(folder, entry, wallet, interactive=interactive)


def write_new_password(folder, entry, password, wallet=None):
    """Explicit user creation only; no overwriting shared or existing entries."""
    if not isinstance(password,str) or not password or entry in {'api-key','e2e-password'}:
        raise KWalletUnavailable('Choose a new device-specific entry')
    _create_password(folder, entry, password, wallet)


def write_provider_password(folder, entry, password, wallet=None):
    """Explicit first-run token creation; never replace an existing entry."""
    if not isinstance(password, str) or not password or entry in {'e2e-password', 'ai-openai-api-key'}:
        raise KWalletUnavailable('Choose a dedicated provider entry')
    _create_password(folder, entry, password, wallet, provider=True)


def _create_password(folder, entry, password, wallet, provider=False):
    # Serialize the hasEntry/write/read-back sequence across local pbdesk cores
    # and concurrent API requests. Never overwrite another creator's new entry.
    path=os.path.join(tempfile.gettempdir(),f'pbdesk-kwallet-create-{os.getuid()}.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
            raise KWalletUnavailable('KWallet entry creation lock is unavailable')
        fcntl.flock(fd,fcntl.LOCK_EX)
        _password(folder,entry,wallet,new_password=password,provider=provider,interactive=provider)
    finally:
        os.close(fd)

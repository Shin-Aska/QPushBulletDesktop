#!/usr/bin/env python3
"""Ephemeral KWallet D-Bus fixture for the external AppImage smoke test.

This is not shipped in the AppImage. Keys live only in this process's memory.
The service refuses every folder except the dedicated history folder and never
connects to an inherited session bus or activates a real wallet service.
"""
import argparse
import os
from pathlib import Path
import re
import stat


SERVICE = 'org.kde.kwalletd6'
INTERFACE = 'org.kde.KWallet'
OBJECT_PATH = '/modules/kwalletd6'
WALLET = 'pbdesk-smoke-wallet'


def validate_bus(address, root):
    """Accept only the harness-owned socket in its private temporary directory."""
    root = Path(root).resolve(strict=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Smoke root must be private and owned')
    expected = root / 'session-bus'
    if address != 'unix:path=' + str(expected):
        raise ValueError('Only the explicitly isolated smoke bus is allowed')
    info = expected.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Smoke session socket is invalid')
    return address


class MemoryWallet:
    """The subset of the real KWallet interface used by history protection."""
    def __init__(self):
        self.folders = {}

    def dispatch(self, method, signature, body):
        signatures = {
            'networkWallet': '', 'wallets': '', 'isOpen': 's', 'open': 'sxs',
            'close': 'ibs', 'hasFolder': 'iss', 'createFolder': 'iss',
            'hasEntry': 'isss', 'writePassword': 'issss', 'readPassword': 'isss',
        }
        if method not in signatures or signature != signatures[method]:
            raise ValueError('Unsupported fixture method')
        if method == 'networkWallet':
            return 's', (WALLET,)
        if method == 'wallets':
            return 'as', ([WALLET],)
        if method == 'isOpen':
            return 'b', (body == (WALLET,),)
        if method == 'open':
            if body != (WALLET, 0, 'pbdesk'):
                raise ValueError('Unexpected wallet authorization')
            return 'i', (1,)
        if method == 'close':
            if body != (1, False, 'pbdesk'):
                raise ValueError('Unexpected wallet close')
            return 'i', (0,)
        if len(body) < 3 or body[0] != 1 or body[1] != 'pbdesk-history' or body[-1] != 'pbdesk':
            raise ValueError('Only dedicated history entries are allowed')
        folder = body[1]
        if method == 'hasFolder':
            return 'b', (folder in self.folders,)
        if method == 'createFolder':
            self.folders.setdefault(folder, {})
            return 'b', (True,)
        entry = body[2]
        if not re.fullmatch(r'history-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', entry):
            raise ValueError('Only dedicated history entries are allowed')
        entries = self.folders.get(folder, {})
        if method == 'hasEntry':
            return 'b', (entry in entries,)
        if method == 'writePassword':
            value = body[3]
            if folder not in self.folders or entry in entries or not re.fullmatch('[0-9a-f]{64}', value):
                raise ValueError('Fixture refuses invalid or overwritten keys')
            entries[entry] = value
            return 'i', (0,)
        if method == 'readPassword':
            return 's', (entries.get(entry, ''),)
        raise ValueError('Unsupported fixture method')


def serve(address, root):
    from jeepney import new_error, new_method_call, new_method_return
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.low_level import HeaderFields, MessageType
    from jeepney.wrappers import unwrap_msg

    with open_dbus_connection(validate_bus(address, root)) as bus:
        names = unwrap_msg(bus.send_and_get_reply(new_method_call(message_bus, 'ListNames')))[0]
        if any(name.startswith('org.kde.kwallet') for name in names):
            raise RuntimeError('Refusing a bus with an existing wallet service')
        reply = bus.send_and_get_reply(new_method_call(message_bus, 'RequestName', 'su', (SERVICE, 4)))
        if unwrap_msg(reply) != (1,):
            raise RuntimeError('Could not exclusively own the fixture name')
        wallet = MemoryWallet()
        print('ready', flush=True)
        while True:
            message = bus.receive()
            if message.header.message_type != MessageType.method_call:
                continue
            fields = message.header.fields
            try:
                if fields.get(HeaderFields.path) != OBJECT_PATH or fields.get(HeaderFields.interface) != INTERFACE:
                    raise ValueError('Unsupported fixture interface')
                signature, values = wallet.dispatch(fields.get(HeaderFields.member), fields.get(HeaderFields.signature, ''), message.body)
                bus.send(new_method_return(message, signature, values))
            except (ValueError, TypeError, IndexError):
                # Do not include an input value, wallet key or message body.
                bus.send(new_error(message, 'org.kde.KWallet.Error', 's', ('Unsupported smoke wallet request',)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bus-address', required=True)
    parser.add_argument('--private-root', required=True, type=Path)
    args = parser.parse_args()
    serve(args.bus_address, args.private_root)


if __name__ == '__main__':
    main()

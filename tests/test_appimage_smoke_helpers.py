"""External smoke-fixture tests; none build or substitute an AppImage."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.history_bootstrap


def load_helper(name):
    spec = importlib.util.spec_from_file_location('appimage_smoke_' + name, ROOT / 'packaging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_helper('smoke')
fixture = load_helper('fake_kwallet')


def test_smoke_environment_cannot_inherit_profile_or_credentials(tmp_path, monkeypatch):
    for name in ('PYTHONPATH', 'PYTHONHOME', 'APPIMAGE', 'REALAPPIMAGE', 'APPDIR', 'DBUS_SESSION_BUS_ADDRESS',
                 'PBDESK_APPLICATION_DIR', 'PBDESK_STATE_DIR', 'OPENAI_API_KEY', 'PUSHBULLET_TOKEN',
                 'LD_LIBRARY_PATH', 'QT_PLUGIN_PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY'):
        monkeypatch.setenv(name, 'fictional-inherited-private-value')
    env = smoke.isolated_environment(tmp_path, (12340, 12341))
    assert 'fictional-inherited-private-value' not in env.values()
    assert 'PYTHONPATH' not in env and 'PYTHONHOME' not in env
    assert 'DBUS_SESSION_BUS_ADDRESS' not in env and 'DISPLAY' not in env
    assert 'PBDESK_APPLICATION_DIR' not in env  # default beside-image path is tested
    assert env['PBDESK_STATE_DIR'] == str(tmp_path / 'state')
    assert env['PBDESK_LIFECYCLE'] == 'foreground'
    assert env['APPIMAGE_EXTRACT_AND_RUN'] == '1'
    assert (tmp_path / 'runtime').stat().st_mode & 0o777 == 0o700


def test_wallet_guard_requires_exact_private_socket(tmp_path):
    tmp_path.chmod(0o700)
    address = 'unix:path=' + str(tmp_path / 'session-bus')
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(tmp_path / 'session-bus'))
        assert fixture.validate_bus(address, tmp_path) == address
        with pytest.raises(ValueError, match='explicitly isolated'):
            fixture.validate_bus('unix:path=/run/user/1000/bus', tmp_path)
        tmp_path.chmod(0o755)
        with pytest.raises(ValueError, match='private'):
            fixture.validate_bus(address, tmp_path)


def test_memory_wallet_supports_only_dedicated_new_history_keys():
    wallet = fixture.MemoryWallet()
    entry = 'history-' + str(uuid.uuid4())
    key = '01' * 32
    assert wallet.dispatch('open', 'sxs', (fixture.WALLET, 0, 'pbdesk')) == ('i', (1,))
    assert wallet.dispatch('createFolder', 'iss', (1, 'pbdesk-history', 'pbdesk')) == ('b', (True,))
    args = (1, 'pbdesk-history', entry, key, 'pbdesk')
    assert wallet.dispatch('writePassword', 'issss', args) == ('i', (0,))
    assert wallet.dispatch('readPassword', 'isss', (1, 'pbdesk-history', entry, 'pbdesk')) == ('s', (key,))
    with pytest.raises(ValueError, match='overwritten'):
        wallet.dispatch('writePassword', 'issss', args)
    with pytest.raises(ValueError, match='dedicated'):
        wallet.dispatch('readPassword', 'isss', (1, 'Pushbullet', 'api-key', 'pbdesk'))
    with pytest.raises(ValueError, match='dedicated'):
        wallet.dispatch('writePassword', 'issss', (1, 'pbdesk-history', 'api-key', key, 'pbdesk'))
    with pytest.raises(ValueError, match='Unsupported'):
        wallet.dispatch('readPassword', 's', ('unexpected',))


def test_cipher_evidence_checks_real_encrypted_main_and_wal(tmp_path):
    from pbdesk.storage.cipher import connect
    database = tmp_path / 'encrypted.sqlite3'
    needle = b'fictional smoke history plaintext'
    with connect(database, b'\x12' * 32) as connection:
        connection.execute('CREATE TABLE probe (body TEXT)')
        connection.execute('INSERT INTO probe VALUES (?)', (needle.decode(),))
        connection.commit()
        evidence = smoke.encrypted_file_evidence(database, [needle])
        assert evidence['plaintext_sqlite_rejected']
        assert evidence['wal_bytes'] > 32
        # A regression writing a recognizable value into a sidecar fails too.
        with Path(str(database) + '-wal').open('ab') as stream:
            stream.write(needle)
        with pytest.raises(smoke.SmokeFailure, match='plaintext leaked'):
            smoke.encrypted_file_evidence(database, [needle])


def test_cipher_evidence_rejects_plaintext_database(tmp_path):
    database = tmp_path / 'plain.sqlite3'
    database.write_bytes(b'SQLite format 3\0' + b'\0' * 4096)
    with pytest.raises(smoke.SmokeFailure, match='plaintext SQLite'):
        smoke.encrypted_file_evidence(database, [])


@pytest.mark.skipif(shutil.which('dbus-daemon') is None, reason='private fixture needs dbus-daemon')
def test_fake_service_exercises_unmodified_kwallet_dbus_adapter(tmp_path, monkeypatch):
    from pbdesk.services import kwallet
    tmp_path.chmod(0o700)
    env = smoke.isolated_environment(tmp_path, smoke.free_ports())
    address = 'unix:path=' + str(tmp_path / 'session-bus')
    processes = []
    try:
        bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--nopidfile', '--address=' + address,
                                '--print-address=1'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append(bus)
        assert select.select([bus.stdout], [], [], 5)[0]
        assert bus.stdout.readline().decode().strip().split(',guid=')[0] == address
        service = subprocess.Popen([sys.executable, str(ROOT / 'packaging/fake_kwallet.py'), '--bus-address', address,
                                    '--private-root', str(tmp_path)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append(service)
        assert select.select([service.stdout], [], [], 5)[0]
        assert service.stdout.readline() == b'ready\n'
        monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', address)
        # Keep the adapter's interprocess creation lock isolated as well.
        monkeypatch.setattr(kwallet, 'tempfile', SimpleNamespace(gettempdir=lambda: str(tmp_path)))
        selected = kwallet.authorize_database_wallet()
        assert selected == fixture.WALLET
        identity = str(uuid.uuid4())
        kwallet.write_database_key(identity, b'\x42' * 32, selected)
        assert kwallet.read_database_key(identity) == b'\x42' * 32
        with pytest.raises(kwallet.KWalletUnavailable):
            kwallet.write_database_key(identity, b'\x43' * 32, selected)
        assert kwallet.read_database_key(identity, interactive=True) == b'\x42' * 32
        with pytest.raises(kwallet.KWalletUnavailable):
            kwallet.read_password('Pushbullet', 'api-key', selected)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(any(shutil.which(name) is None for name in ('dbus-daemon', 'Xvfb', 'xdotool')),
                    reason='private display fixture needs runner desktop utilities')
def test_harness_private_services_start_and_clean_up_exact_owned_children(tmp_path, monkeypatch):
    # This tests only the fixture lifecycle; this file is never executed as an
    # AppImage and does not substitute for the actual-artifact CI smoke run.
    input_file = tmp_path / 'fixture-input-only'
    input_file.write_bytes(b'fixture input, not an AppImage')
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/nonexistent-inherited-session')
    monkeypatch.setenv('DISPLAY', ':9999')
    harness = smoke.Harness(input_file, private, {'checks': []})
    owned = set()
    try:
        harness.private_services()
        assert harness.env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=' + str(private / 'session-bus')
        assert harness.env['DISPLAY'] != ':9999'
        owned = smoke.descendants(os.getpid())
        assert len(owned) == 3
        assert harness.window('nonexistent-smoke-window') is None
    finally:
        harness.close()
    assert not owned.intersection(smoke.descendants(os.getpid()))


def test_failure_diagnostics_include_errno_and_locations_without_values():
    secret = 'fictional-private-token-do-not-record'
    def failed_operation():
        raise OSError(errno.EIO, secret, '/private/' + secret)
    try:
        failed_operation()
    except OSError as exception:
        exception.add_note(secret)
        details = smoke.exception_diagnostics(exception)
    assert details['exception'] == 'OSError'
    assert details['errno'] == errno.EIO
    assert details['frames'][-1]['function'] == 'failed_operation'
    assert details['frames'][-1]['filename'] == Path(__file__).name
    assert all(set(frame) == {'filename', 'line', 'function'} for frame in details['frames'])
    assert secret not in json.dumps(details)
    assert '/private/' not in json.dumps(details)


def test_failure_diagnostics_preserve_original_when_cleanup_masks_failure():
    try:
        try:
            raise OSError(errno.EIO, 'fictional-private-original')
        finally:
            raise OSError(errno.ENOENT, 'fictional-private-cleanup')
    except OSError as exception:
        details = smoke.exception_diagnostics(exception)
    assert details['errno'] == errno.ENOENT
    assert details['context']['errno'] == errno.EIO
    assert details['context']['frames']
    assert 'fictional-private-' not in json.dumps(details)


def test_command_failure_reports_role_and_returncode_without_argv_or_output(monkeypatch):
    harness = object.__new__(smoke.Harness)
    child = SimpleNamespace(returncode=19, communicate=lambda timeout: (b'fictional-private-output', None))
    monkeypatch.setattr(harness, 'spawn', lambda *args, **kwargs: child)
    with pytest.raises(smoke.SmokeFailure) as caught:
        harness.run(['xdotool', 'key', '--window', 'fictional-private-argument'])
    details = smoke.exception_diagnostics(caught.value)
    assert details['command_role'] == 'xdotool_key'
    assert details['returncode'] == 19
    assert 'fictional-private-' not in json.dumps(details)


def test_command_spawn_error_reports_role_and_errno_without_filename(monkeypatch, tmp_path):
    harness = object.__new__(smoke.Harness)
    harness.cwd, harness.env, harness.log = tmp_path, {}, None
    def unavailable_command(*args, **kwargs):
        raise OSError(errno.ENOENT, 'fictional-private-error', '/private/fictional-private-file')
    monkeypatch.setattr(smoke.subprocess, 'Popen', unavailable_command)
    with pytest.raises(OSError) as caught:
        harness.spawn(['xdotool', 'windowfocus', '--sync', 'fictional-private-window'])
    details = smoke.exception_diagnostics(caught.value)
    assert details['command_role'] == 'xdotool_windowfocus'
    assert details['errno'] == errno.ENOENT
    assert 'fictional-private-' not in json.dumps(details)


def test_cleanup_failure_cannot_leave_successful_evidence(monkeypatch, tmp_path, capsys):
    class FixtureHarness:
        def __init__(self, *args):
            pass
        def exercise(self):
            pass
        def close(self):
            raise OSError(errno.EIO, 'fictional-private-cleanup-value')
    candidate = tmp_path / 'fixture-input-only'
    candidate.write_bytes(b'not executed')
    output = tmp_path / 'evidence.json'
    monkeypatch.setattr(smoke, 'Harness', FixtureHarness)
    monkeypatch.setattr(smoke.shutil, 'which', lambda *args, **kwargs: '/fixture/command')
    assert smoke.main([str(candidate), '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['status'] == 'fail'
    assert report['failure_diagnostics']['errno'] == errno.EIO
    assert report['failure_diagnostics']['frames'][-1]['function'] == 'close'
    assert 'fictional-private-cleanup-value' not in output.read_text()
    assert 'fictional-private-cleanup-value' not in capsys.readouterr().out

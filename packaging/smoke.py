#!/usr/bin/env python3
"""Exercise an actual AppImage using only synthetic, disposable local state.

Run with the installed *test* Python, for example:
  dbus-run-session -- xvfb-run -a .test-venv/bin/python packaging/smoke.py \
      /absolute/path/pbdesk.AppImage --output /absolute/path/smoke-evidence.json

The harness starts its own private D-Bus and Xvfb, even inside those wrappers.
The application never imports this harness or a source-tree wallet adapter.
No keys, bearer tokens, recovery material, or child logs enter the evidence.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time


class SmokeFailure(RuntimeError):
    def __init__(self, message, *, command_role=None, returncode=None):
        super().__init__(message)
        self.smoke_command_role = command_role
        self.smoke_returncode = returncode


def command_role(arguments):
    """Describe a command without recording arbitrary arguments or paths."""
    executable = Path(str(arguments[0])).name if arguments else ''
    option = str(arguments[1]) if len(arguments) > 1 else ''
    if executable == 'xdotool':
        return {'windowfocus': 'xdotool_windowfocus', 'key': 'xdotool_key',
                'search': 'xdotool_search'}.get(option, 'xdotool')
    if executable == 'dbus-daemon':
        return 'private_dbus_daemon'
    if executable == 'Xvfb':
        return 'private_xvfb'
    if Path(option).name == 'fake_kwallet.py':
        return 'private_fake_kwallet'
    return {'--core': 'appimage_core', '--web': 'appimage_web_control',
            '--admin': 'appimage_admin', '--mcp': 'appimage_mcp',
            '--diagnostics': 'appimage_diagnostics', '--appimage-extract': 'appimage_extract',
            '--demo': 'appimage_desktop_demo', '': 'appimage_desktop'}.get(option, 'external_command')


def exception_diagnostics(exception, *, _seen=None):
    """Frame locations and numeric error codes only: never exception values.

    In particular, OSError.filename, command argv/output, frame source lines,
    locals, exception notes and the exception's string representation are absent.
    Preserve a cleanup exception's context so it cannot hide the original site.
    """
    seen = set() if _seen is None else _seen
    seen.add(id(exception))
    details = {'exception': type(exception).__name__, 'frames': []}
    if isinstance(exception, OSError) and type(exception.errno) is int:
        details['errno'] = exception.errno
    role = getattr(exception, 'smoke_command_role', None)
    if role is not None:
        details['command_role'] = role
    returncode = getattr(exception, 'smoke_returncode', None)
    if type(returncode) is int:
        details['returncode'] = returncode
    frame = exception.__traceback__
    while frame is not None:
        details['frames'].append({'filename': Path(frame.tb_frame.f_code.co_filename).name,
                                  'line': frame.tb_lineno, 'function': frame.tb_frame.f_code.co_name})
        frame = frame.tb_next
    linked = exception.__cause__ or exception.__context__
    if linked is not None and id(linked) not in seen and len(seen) < 8:
        details['cause' if exception.__cause__ is not None else 'context'] = exception_diagnostics(linked, _seen=seen)
    return details


def free_ports():
    sockets = [socket.socket(), socket.socket()]
    try:
        for sock in sockets:
            sock.bind(('127.0.0.1', 0))
        return tuple(sock.getsockname()[1] for sock in sockets)
    finally:
        for sock in sockets:
            sock.close()


def isolated_environment(root, ports):
    """Allowlist environment: never inherit a real profile, bus or provider key."""
    root = Path(root)
    env = {
        'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'HOME': str(root / 'home'), 'TMPDIR': str(root / 'tmp'),
        'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
        'XDG_CACHE_HOME': str(root / 'cache'), 'XDG_STATE_HOME': str(root / 'xdg-state'),
        'XDG_RUNTIME_DIR': str(root / 'runtime'),
        'PBDESK_STATE_DIR': str(root / 'state'),
        'PBDESK_CORE_PORT': str(ports[0]), 'PBDESK_WEB_PORT': str(ports[1]),
        'PBDESK_LIFECYCLE': 'foreground', 'PBDESK_LIFECYCLE_DEMO': '1',
        'PBDESK_LEASE_TIMING': json.dumps({'renew': .3, 'expiry': 2.4, 'release_grace': .5, 'startup_grace': 8}),
        'QT_QPA_PLATFORM': 'xcb', 'QT_OPENGL': 'software', 'LIBGL_ALWAYS_SOFTWARE': '1',
        # This is the AppImage runtime's extraction mode, not a source fallback.
        'APPIMAGE_EXTRACT_AND_RUN': '1',
    }
    for name in ('HOME', 'TMPDIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
                 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'PBDESK_STATE_DIR'):
        Path(env[name]).mkdir(mode=0o700, parents=True, exist_ok=True)
    return env


def encrypted_file_evidence(database, needles):
    """Verify raw main/WAL bytes and independently reject plaintext SQLite."""
    database = Path(database)
    data = database.read_bytes()
    if not data or data.startswith(b'SQLite format 3\0'):
        raise SmokeFailure('Database is absent, empty or plaintext SQLite')
    wal = Path(str(database) + '-wal')
    wal_data = wal.read_bytes()
    if len(wal_data) <= 32:
        raise SmokeFailure('Expected a populated encrypted WAL')
    for needle in needles:
        if needle in data or needle in wal_data:
            raise SmokeFailure('Synthetic plaintext leaked into history bytes')
    try:
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
    except sqlite3.DatabaseError:
        pass
    else:
        raise SmokeFailure('Plain SQLite unexpectedly opened encrypted history')
    return {'database_bytes': len(data), 'wal_bytes': len(wal_data), 'plaintext_sqlite_rejected': True}


def descendants(pid):
    """Read only our own process tree; never discover or signal another service."""
    found = set()
    pending = [pid]
    while pending:
        parent = pending.pop()
        try:
            children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        for child in map(int, children):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def wait_for(operation, label, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = operation()
        if result:
            return result
        time.sleep(.1)
    raise SmokeFailure('Timed out: ' + label)


class Harness:
    def __init__(self, image, root, report):
        import httpx
        from pbdesk.config import Settings
        self.httpx = httpx
        self.root, self.report = root, report
        self.env = isolated_environment(root, free_ports())
        self.cwd = root / 'unrelated working directory'
        self.cwd.mkdir(mode=0o700)
        application = root / 'application with spaces'
        application.mkdir(mode=0o700)
        self.image = application / 'pbdesk candidate.AppImage'
        shutil.copyfile(image, self.image)
        self.image.chmod(0o755)
        self.settings = Settings(Path(self.env['PBDESK_STATE_DIR']), core_port=int(self.env['PBDESK_CORE_PORT']),
                                 web_port=int(self.env['PBDESK_WEB_PORT']), application_dir=application)
        self.processes = []
        self.log = (root / 'children.log').open('w')

    def check(self, name, condition=True, **evidence):
        self.report['checks'].append({'name': name, 'status': 'pass' if condition else 'fail', **evidence})
        if not condition:
            raise SmokeFailure(name)

    def spawn(self, arguments, *, env=None, stdout=None, pass_fds=()):
        try:
            child = subprocess.Popen(list(map(str, arguments)), cwd=self.cwd, env=env or self.env,
                                     stdin=subprocess.DEVNULL, stdout=stdout or self.log, stderr=self.log,
                                     start_new_session=True, pass_fds=pass_fds)
        except OSError as exc:
            exc.smoke_command_role = command_role(arguments)
            raise
        self.processes.append(child)
        return child

    def run(self, arguments, *, env=None, timeout=90):
        child = self.spawn(arguments, env=env, stdout=subprocess.PIPE)
        try:
            stdout, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise SmokeFailure('Packaged command timed out', command_role=command_role(arguments),
                               returncode=child.returncode) from None
        if child.returncode:
            raise SmokeFailure('Packaged command failed', command_role=command_role(arguments),
                               returncode=child.returncode)
        return stdout.decode('utf-8')

    def private_services(self):
        # Adopting orphaned application children makes cleanup exact even after
        # the desktop exits or a restart helper replaces its original parent.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, Linux
            raise SmokeFailure('Could not own orphaned smoke-test processes')
        address = 'unix:path=' + str(self.root / 'session-bus')
        bus = self.spawn(['dbus-daemon', '--session', '--nofork', '--nopidfile', '--address=' + address,
                          '--print-address=1'], stdout=subprocess.PIPE)
        ready, _, _ = select.select([bus.stdout], [], [], 10)
        if not ready or bus.stdout.readline().decode().strip().split(',guid=')[0] != address:
            raise SmokeFailure('Private session bus did not start')
        self.env['DBUS_SESSION_BUS_ADDRESS'] = address
        # Xvfb chooses a free display and prints only its number to this pipe.
        read_fd, write_fd = os.pipe()
        try:
            self.spawn(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1280x900x24', '-nolisten', 'tcp'],
                       pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = None
            ready, _, _ = select.select([read_fd], [], [], 10)
            number = os.read(read_fd, 64).decode().strip() if ready else ''
            if not number.isdecimal():
                raise SmokeFailure('Private Xvfb did not start')
            self.env['DISPLAY'] = ':' + number
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
        fixture = Path(__file__).with_name('fake_kwallet.py').resolve()
        wallet = self.spawn([sys.executable, fixture, '--bus-address', address, '--private-root', self.root], stdout=subprocess.PIPE)
        ready, _, _ = select.select([wallet.stdout], [], [], 10)
        if not ready or wallet.stdout.readline() != b'ready\n':
            raise SmokeFailure('Private in-memory KWallet fixture did not start')
        self.check('isolated_bus_display_wallet', inherited_session_used=False, wallet_storage='memory only')

    def health(self):
        from pbdesk.credentials import health_headers, verify_health
        headers = health_headers(self.settings, 'desktop')
        try:
            response = self.httpx.get(self.settings.core_url + '/api/v1/health', headers=headers, timeout=1, trust_env=False)
        except self.httpx.TransportError:
            return None
        if response.status_code != 200 or not verify_health(self.settings, 'desktop', headers, response.json()):
            raise SmokeFailure('Core health signature did not verify')
        return response.json()

    def ready(self):
        value = self.health()
        return value if value and value.get('ready') and value.get('history_ready') else None

    def request(self, method, path, *, expected=200, role='desktop', **kwargs):
        from pbdesk.credentials import service_token
        response = self.httpx.request(method, self.settings.core_url + path,
                                     headers={'Authorization': 'Bearer ' + service_token(self.settings, role)},
                                     timeout=90, trust_env=False, **kwargs)
        if response.status_code != expected:
            raise SmokeFailure('Unexpected status for ' + path)
        return response.json()

    def window(self, title):
        result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', title],
                                cwd=self.cwd, env=self.env, capture_output=True, timeout=3)
        return result.stdout.decode().splitlines()[0] if result.returncode == 0 else None

    def key(self, window, value):
        self.run(['xdotool', 'windowfocus', '--sync', window])
        self.run(['xdotool', 'key', '--window', window, '--clearmodifiers', value])

    def cached_apps(self):
        cache = Path(self.env['XDG_CACHE_HOME']) / 'pbdesk/runtimes'
        apps = []
        for pid in descendants(os.getpid()):
            try:
                executable = Path(os.readlink(f'/proc/{pid}/exe'))
                if executable.name != 'pbdesk':
                    continue
                executable.relative_to(cache)
                args = Path(f'/proc/{pid}/cmdline').read_bytes()
                cwd = Path(os.readlink(f'/proc/{pid}/cwd'))
                if str(Path(__file__).resolve().parents[1]).encode() in args or not cwd.is_relative_to(self.root):
                    raise SmokeFailure('Packaged process retained source checkout assumptions')
                apps.append({'pid': pid, 'runtime': executable.parent.name})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except ValueError:
                raise SmokeFailure('Application process is outside the persistent runtime cache') from None
        if not apps:
            raise SmokeFailure('No cached frozen application process found')
        return apps

    def protect(self, *, saved):
        self.request('GET', '/api/v1/items', expected=423)
        self.request('POST', '/api/v1/history/protect', expected=403, role='web')
        status = self.request('POST', '/api/v1/history/protect')
        self.check('protect_requires_explicit_recovery_choice_' + str(saved).lower(),
                   status['recovery_offer_pending'] and status['unlocked'] and not status['history_ready'])
        self.request('GET', '/api/v1/items', expected=423)
        recovery = self.request('POST', '/api/v1/history/recovery-key').get('recovery_key')
        self.check('recovery_export_available_' + str(saved).lower(), isinstance(recovery, str) and len(recovery) > 32)
        if saved:
            backup = self.root / 'synthetic-recovery-backup.txt'
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(recovery)
            self.check('private_synthetic_recovery_backup', backup.read_text() == recovery
                       and backup.stat().st_mode & 0o777 == 0o600)
        # Recovery material stays inside disposable state and never enters logs.
        del recovery
        self.request('POST', '/api/v1/history/recovery-offer', json={'saved': saved})
        wait_for(self.ready, 'history activation')

    def sample_and_paths(self, name):
        items = self.request('GET', '/api/v1/items')['items']
        self.check(name, any(item.get('source_id') == 'stored:pbdesk-sanitized-demo-v1' and
                            item.get('body') == 'A sanitized event received once by your private core.' for item in items))
        settings = self.request('GET', '/api/v1/core/settings')
        self.check(name + '_paths', settings['paths']['application'] == str(self.settings.application_dir)
                   and settings['paths']['state'] == str(self.settings.state_dir)
                   and settings['paths']['database'] == str(self.settings.application_dir / 'pbdesk.sqlite3'))
        return settings

    def web_health(self):
        from pbdesk.services.web_identity import web_health_headers, verify_web_health
        headers = web_health_headers(self.settings)
        try:
            response = self.httpx.get(f'http://127.0.0.1:{self.settings.web_port}/health', headers=headers,
                                     timeout=1, trust_env=False)
        except self.httpx.TransportError:
            return None
        body = response.json()
        if response.status_code != 200 or not verify_web_health(self.settings, headers, body):
            raise SmokeFailure('Web health signature did not verify')
        return body

    def enable_web(self):
        value = json.loads(self.run([self.image, '--web', 'enable']))
        self.check('public_web_enable', value.get('enabled') and value.get('ownership') in {'owned', 'adopted'})
        identity = wait_for(self.web_health, 'signed web health')
        page = self.httpx.get(f'http://127.0.0.1:{self.settings.web_port}/', timeout=10, trust_env=False)
        self.check('bundled_web_template', page.status_code == 200 and 'text/html' in page.headers.get('content-type', ''))
        return identity

    def restart(self, label, web_identity=None):
        old = self.ready()['instance_id']
        for _ in range(30):
            value = self.request('GET', '/api/v1/core/settings')
            if value['uptime_seconds'] >= 1:
                break
            time.sleep(.1)
        self.request('PATCH', '/api/v1/core/settings', json={'expected_instance_id': old, 'notifications_enabled': False})
        self.request('POST', '/api/v1/core/restart', expected=202,
                     json={'expected_instance_id': old, 'request_id': label})
        replacement = wait_for(lambda: (value := self.ready()) and value['instance_id'] != old and value,
                               'frozen restart helper', timeout=45)
        settings = self.request('GET', '/api/v1/core/settings')
        self.check(label, replacement['instance_id'] != old and not settings['notifications']['requested'])
        if web_identity:
            state = wait_for(lambda: (value := self.request('GET', '/api/v1/web'))['enabled'] and value,
                             'web adoption after restart')
            self.check(label + '_web_retained', state['instance_id'] == web_identity['instance_id']
                       and state['ownership'] == 'adopted'
                       and self.web_health()['instance_id'] == web_identity['instance_id'])
        self.check(label + '_cached_children', bool(self.cached_apps()))

    def stop_all(self, label):
        from pbdesk.services.client_lifecycle import stop_generation
        generation = stop_generation(self.settings)
        self.request('POST', '/api/v1/shutdown')
        wait_for(lambda: self.health() is None, 'Stop All core exit')
        wait_for(lambda: self.web_health() is None, 'Stop All web exit')
        time.sleep(3)  # exceed the isolated lease expiry and observe no revival
        self.check(label, self.health() is None and self.web_health() is None
                   and generation != stop_generation(self.settings))

    def exercise(self):
        self.private_services()
        diagnostics = json.loads(self.run([self.image, '--diagnostics']))
        self.check('bundled_sqlcipher_fts5', diagnostics.get('fts5') is True
                   and str(diagnostics.get('sqlcipher', '')).startswith('4.20.0'),
                   sqlcipher=diagnostics.get('sqlcipher'), fts5=diagnostics.get('fts5'))
        dependencies = diagnostics.get('dependencies', {})
        self.check('bundled_dependency_metadata', bool(dependencies)
                   and all(value != 'not installed' for value in dependencies.values()), dependencies=dependencies)
        for mode in ('--core', '--web', '--admin'):
            output = self.run([self.image, mode, '--help'])
            self.check('public_cli_help_' + mode[2:], 'usage:' in output.lower())
        self.check('outside_checkout_environment', 'PYTHONPATH' not in self.env and 'PYTHONHOME' not in self.env,
                   appimage_path_has_spaces=' ' in str(self.image), extraction_mode='actual AppImage runtime')

        core = self.spawn([self.image, '--core', '--demo', '--standalone'])
        locked = wait_for(self.health, 'locked demo standalone core')
        self.check('signed_locked_core', locked.get('ready') and locked.get('history_encryption') == 1
                   and locked.get('history_ready') is False)
        desktop = self.spawn([self.image])
        window = wait_for(lambda: self.window('^Protect your local history$'), 'actual protection dialog')
        self.check('actual_default_desktop_protection_dialog', desktop.poll() is None)
        self.key(window, 'Escape')
        desktop.wait(timeout=20)
        self.check('desktop_bootstrap_exit', desktop.returncode == 0)
        self.protect(saved=False)
        initial = self.sample_and_paths('default_beside_appimage_history')
        self.check('standalone_core_profile', initial['lifecycle']['mode'] == 'standalone')
        evidence = encrypted_file_evidence(self.settings.application_dir / 'pbdesk.sqlite3',
                       [b'pbdesk demo', b'A sanitized event received once by your private core.'])
        self.check('actual_encrypted_database_and_wal', **evidence)
        self.check('frozen_core_cache', bool(self.cached_apps()))
        self.stop_all('standalone_stop_all')
        core.wait(timeout=15)

        # A real second desktop cold-starts the coupled core using its frozen
        # child dispatcher. Web must retain that core after desktop exit.
        desktop = self.spawn([self.image, '--demo'])
        wait_for(self.ready, 'desktop-launched encrypted core')
        window = wait_for(lambda: self.window('^pbdesk — Notifications$'), 'actual inbox window')
        state = wait_for(lambda: (value := self.request('GET', '/api/v1/core/settings'))['lifecycle']['desktop_clients'] == 1 and value,
                         'desktop client lease')
        self.check('frozen_desktop_cold_start_coupled_core', state['lifecycle']['mode'] == 'coupled')
        web = self.enable_web()
        self.key(window, 'ctrl+q')
        desktop.wait(timeout=20)
        time.sleep(3)
        state = self.request('GET', '/api/v1/core/settings')
        self.check('web_retains_core_after_desktop_exit', desktop.returncode == 0
                   and state['lifecycle']['desktop_clients'] == 0 and state['lifecycle']['web_clients'] == 1)
        self.restart('frozen_restart_with_web', web)
        self.stop_all('coupled_stop_all_no_revival')

        # Replace the candidate in place, keeping the same profile. This tests
        # replacement mechanics, not migration from an older released version.
        credential_digest = hashlib.sha256((self.settings.state_dir / 'service-credentials.json').read_bytes()).hexdigest()
        self.image.unlink()
        shutil.copyfile(self.report['_input_image'], self.image)
        self.image.chmod(0o755)
        extraction = self.root / 'artifact extraction'
        extraction.mkdir(mode=0o700)
        result = subprocess.run([str(self.image), '--appimage-extract'], cwd=extraction, env=self.env,
                                stdout=self.log, stderr=self.log, timeout=90)
        self.check('actual_appimage_extraction', result.returncode == 0)
        tree = extraction / 'squashfs-root'
        env = dict(self.env, APPIMAGE=str(self.image), REALAPPIMAGE=str(self.image))
        core = self.spawn([tree / 'AppRun', '--core', '--demo', '--standalone'], env=env)
        wait_for(self.ready, 'replacement candidate profile unlock')
        settings = self.sample_and_paths('replacement_candidate_preserves_history')
        self.check('replacement_preserves_settings_credentials', not settings['notifications']['requested']
                   and credential_digest == hashlib.sha256((self.settings.state_dir / 'service-credentials.json').read_bytes()).hexdigest())
        self.check('replacement_uses_persistent_cache', bool(self.cached_apps()))
        shutil.rmtree(tree)
        self.check('original_extracted_launch_tree_removed', not tree.exists())
        web = self.enable_web()
        self.restart('restart_after_launch_tree_removed', web)
        self.sample_and_paths('reads_after_launch_tree_removed')
        self.stop_all('replacement_stop_all')

        # Explicit location overrides get their own new encrypted profile and
        # exercise the other recovery acknowledgement choice.
        from pbdesk.config import Settings
        application = self.root / 'explicit application override'
        state = self.root / 'explicit state override'
        application.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        self.env.update(PBDESK_APPLICATION_DIR=str(application), PBDESK_STATE_DIR=str(state))
        self.settings = Settings(state, core_port=self.settings.core_port, web_port=self.settings.web_port,
                                 application_dir=application)
        core = self.spawn([self.image, '--core', '--demo', '--standalone'])
        wait_for(self.health, 'explicit override locked core')
        self.protect(saved=True)
        self.sample_and_paths('explicit_application_state_overrides')
        self.stop_all('override_stop_all')
        core.wait(timeout=15)

    def close(self):
        # Only processes descended from this harness are eligible for cleanup.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            owned = descendants(os.getpid())
            for pid in owned:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 5
            while descendants(os.getpid()) and time.monotonic() < deadline:
                for child in self.processes:
                    child.poll()
                try:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
                time.sleep(.05)
        self.log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('appimage', type=Path)
    parser.add_argument('--output', type=Path, default=Path('smoke-evidence.json'))
    args = parser.parse_args(argv)
    image = args.appimage.resolve(strict=True)
    output = args.output.resolve()
    with image.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'schema_version': 1, 'artifact': image.name,
              'sha256': digest,
              'status': 'fail', 'checks': [],
              'unverified': ['Native KDE/Wayland, tray and notifications', 'Real provider and paid AI operations',
                             'Upgrade from a previously released version', 'Recovery-key restore and plaintext migration interruption'],
              '_input_image': str(image)}
    harness = None
    try:
        for command in ('dbus-daemon', 'Xvfb', 'xdotool'):
            if not shutil.which(command, path='/usr/bin:/bin'):
                raise SmokeFailure('Required runner command is unavailable: ' + command)
        with tempfile.TemporaryDirectory(prefix='pbdesk-appimage-smoke-') as temporary:
            try:
                harness = Harness(image, Path(temporary), report)
                harness.exercise()
                report['status'] = 'pass'
            finally:
                if harness:
                    harness.close()
    except Exception as exc:
        report['status'] = 'fail'
        report['failure'] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        report['failure_diagnostics'] = exception_diagnostics(exc)
    finally:
        report.pop('_input_image', None)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'evidence': str(output)}))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())

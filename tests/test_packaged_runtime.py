"""Isolated runtime/launcher fixtures; never open a real profile or wallet."""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys

import pytest

from pbdesk import config, packaged, runtime


@pytest.fixture(autouse=True)
def preserve_process_arguments(monkeypatch):
    monkeypatch.setattr(sys, 'argv', list(sys.argv))


def manifest(root, changes=None):
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs) + names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative in (packaged.MANIFEST, packaged.IDENTITY) or path.is_dir() and not path.is_symlink():
                continue
            files[relative] = ({'type': 'symlink', 'target': os.readlink(path)} if path.is_symlink()
                               else {'type': 'file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'mode': path.stat().st_mode & 0o777})
    if changes:
        changes(files)
    raw = (json.dumps({'version': 1, 'files': files}, sort_keys=True, separators=(',', ':')) + '\n').encode()
    (root / packaged.MANIFEST).write_bytes(raw)
    (root / packaged.IDENTITY).write_text(hashlib.sha256(raw).hexdigest() + '\n')
    (root / packaged.MANIFEST).chmod(0o644)
    (root / packaged.IDENTITY).chmod(0o644)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir(mode=0o755)
    (root / 'pbdesk').write_bytes(b'executable fixture')
    (root / 'pbdesk').chmod(0o755)
    (root / '_internal').mkdir(mode=0o755)
    (root / '_internal' / 'library.so').write_bytes(b'library fixture')
    (root / '_internal' / 'library.so').chmod(0o644)
    (root / 'library.so').symlink_to('_internal/library.so')
    manifest(root)
    return root


def _cache_pair(pair):
    source, cache = pair
    return str(packaged.cache_runtime(source, cache))


def test_command_source_and_frozen(monkeypatch):
    monkeypatch.setattr(sys, 'executable', '/runtime/pbdesk')
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert runtime.command('pbdesk.cli', '--fixture', 'a b') == ['/runtime/pbdesk', '-m', 'pbdesk.cli', '--fixture', 'a b']
    monkeypatch.setattr(sys, 'frozen', True)
    assert runtime.command('pbdesk.web', '--standalone') == ['/runtime/pbdesk', '--pbdesk-run-module', 'pbdesk.web', '--standalone']
    with pytest.raises(ValueError):
        runtime.command('os', 'anything')


def test_child_and_host_environments(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', '/tmp/mount/_internal', raising=False)
    env = {'LD_LIBRARY_PATH': '/tmp/mount/_internal:/host/lib', 'LD_LIBRARY_PATH_ORIG': '/host/lib',
           'APPDIR': '/tmp/mount', 'APPIMAGE': '/apps/pbdesk.AppImage', '_PYI_APPLICATION_HOME_DIR': '/tmp/mount/_internal',
           'QT_PLUGIN_PATH': '/tmp/mount/_internal/plugins', 'PATH': '/tmp/mount/usr/bin:/usr/bin',
           'DBUS_SESSION_BUS_ADDRESS': 'session', 'WAYLAND_DISPLAY': 'wayland-0', 'XDG_STATE_HOME': '/state'}
    host = runtime.system_environment(env)
    assert host['LD_LIBRARY_PATH'] == '/host/lib'
    assert host['PATH'] == '/usr/bin'
    assert 'QT_PLUGIN_PATH' not in host and '_PYI_APPLICATION_HOME_DIR' not in host
    child = runtime.child_environment(env)
    assert child['PYINSTALLER_RESET_ENVIRONMENT'] == '1' and 'APPDIR' not in child
    assert all(child[key] == env[key] for key in ('APPIMAGE', 'DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY', 'XDG_STATE_HOME'))
    monkeypatch.setattr(sys, 'frozen', False)
    assert runtime.child_environment(env) == env


def test_host_environment_without_original_library_path(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert 'LD_LIBRARY_PATH' not in runtime.system_environment({'LD_LIBRARY_PATH': '/bundle'})


def test_frozen_application_directory_uses_outer_image(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/cache/runtime/pbdesk')
    monkeypatch.delenv('PBDESK_APPLICATION_DIR', raising=False)
    monkeypatch.delenv('REALAPPIMAGE', raising=False)
    monkeypatch.setenv('APPIMAGE', str(tmp_path / 'actual.AppImage'))
    assert config.application_directory() == tmp_path
    monkeypatch.setenv('REALAPPIMAGE', str(tmp_path / 'original/app.AppImage'))
    assert config.application_directory() == tmp_path / 'original'
    monkeypatch.delenv('REALAPPIMAGE')
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path / 'override'))
    assert config.application_directory() == tmp_path / 'override'
    monkeypatch.delenv('PBDESK_APPLICATION_DIR')
    monkeypatch.delenv('APPIMAGE')
    with pytest.raises(ValueError, match='original AppImage'):
        config.application_directory()


def test_cache_persistent_verified_and_preserves_symlinks(bundle, tmp_path):
    target = packaged.cache_runtime(bundle, tmp_path / 'cache')
    assert target.name == packaged.verify_runtime(target, private=True)
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / 'library.so').is_symlink()
    shutil.rmtree(bundle)
    assert (target / 'library.so').read_bytes() == b'library fixture'
    assert packaged.verify_runtime(target, private=True) == target.name


def test_concurrent_cache_publication(bundle, tmp_path):
    pairs = [(str(bundle), str(tmp_path / 'cache'))] * 6
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('fork')) as pool:
        results = list(pool.map(_cache_pair, pairs))
    assert len(set(results)) == 1
    assert not list(Path(results[0]).parent.glob('.staging-*'))
    assert packaged.verify_runtime(results[0], private=True) == Path(results[0]).name


def test_staging_failure_is_not_published(bundle, tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise OSError('copy interrupted')
    monkeypatch.setattr(packaged.shutil, 'copytree', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        packaged.cache_runtime(bundle, tmp_path / 'cache')
    assert sorted(path.name for path in (tmp_path / 'cache/pbdesk/runtimes').iterdir()) == ['runtime.lock']


@pytest.mark.parametrize('cached', [False, True])
def test_corruption_is_rejected(bundle, tmp_path, cached):
    root = packaged.cache_runtime(bundle, tmp_path / 'cache') if cached else bundle
    (root / '_internal/library.so').write_bytes(b'corrupted')
    with pytest.raises(packaged.PackagedRuntimeError, match='content verification'):
        packaged.cache_runtime(bundle, tmp_path / 'cache')


@pytest.mark.parametrize('change', ['mode', 'hardlink', 'extra', 'missing', 'directory-mode', 'metadata-link'])
def test_unsafe_cache_is_rejected(bundle, tmp_path, change):
    target = packaged.cache_runtime(bundle, tmp_path / 'cache')
    library = target / '_internal/library.so'
    if change == 'mode':
        library.chmod(0o666)
    elif change == 'hardlink':
        os.link(library, tmp_path / 'hardlink')
    elif change == 'extra':
        (target / 'surprise').write_text('unexpected')
    elif change == 'missing':
        library.unlink()
    elif change == 'directory-mode':
        (target / '_internal').chmod(0o755)
    else:
        (target / packaged.MANIFEST).unlink()
        (target / packaged.MANIFEST).symlink_to(bundle / packaged.MANIFEST)
    with pytest.raises((packaged.PackagedRuntimeError, FileNotFoundError)):
        packaged.cache_runtime(bundle, tmp_path / 'cache')


@pytest.mark.parametrize('target', ['../outside', '/etc/passwd', 'missing'])
def test_unsafe_symlinks_are_rejected(bundle, tmp_path, target):
    (tmp_path / 'outside').write_text('outside')
    (bundle / 'library.so').unlink()
    (bundle / 'library.so').symlink_to(target)
    manifest(bundle)
    with pytest.raises(packaged.PackagedRuntimeError):
        packaged.cache_runtime(bundle, tmp_path / 'cache')


@pytest.mark.parametrize('path', ['../escape', '/absolute', '_internal/../escape', 'double//slash', 'back\\slash'])
def test_manifest_path_traversal_is_rejected(bundle, path):
    manifest(bundle, lambda entries: entries.update({path: entries['pbdesk']}))
    with pytest.raises(packaged.PackagedRuntimeError, match='manifest is invalid'):
        packaged.verify_runtime(bundle)


def test_cache_root_and_lock_permissions(bundle, tmp_path):
    cache = tmp_path / 'cache'
    (cache / 'pbdesk').mkdir(parents=True, mode=0o755)
    with pytest.raises(packaged.PackagedRuntimeError, match='0700'):
        packaged.cache_runtime(bundle, cache)
    (cache / 'pbdesk').chmod(0o700)
    (cache / 'pbdesk/runtimes').mkdir(mode=0o700)
    (cache / 'pbdesk/runtimes/runtime.lock').symlink_to(tmp_path / 'foreign')
    with pytest.raises(OSError):
        packaged.cache_runtime(bundle, cache)


def test_bootstrap_reexec_preserves_arguments(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(bundle / 'pbdesk'))
    calls = []
    monkeypatch.setattr(os, 'execve', lambda *args: calls.append(args))
    assert packaged.main(['--pbdesk-bootstrap', '--core', '--fixture', 'a b']) == 1
    executable, argv, env = calls[0]
    assert argv == [executable, '--core', '--fixture', 'a b']
    assert executable != str(bundle / 'pbdesk') and env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_bootstrap_fails_before_dispatch_when_directory_not_writable(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'read-only'
    root.mkdir(mode=0o555)
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(root))
    monkeypatch.setattr(packaged, '_dispatch', lambda _: pytest.fail('must not start history/wallet/provider'))
    assert packaged.main([]) == 1
    assert 'writable application directory' in capsys.readouterr().err


@pytest.mark.parametrize('switch,entry', [('--core', 'core_main'), ('--web', 'web_main'), ('--mcp', 'mcp_main'), ('--admin', 'admin_main'), ('--diagnostics', 'diagnostics_main'), ('', 'desktop_main')])
def test_public_dispatch_arguments_and_return_code(monkeypatch, switch, entry):
    from pbdesk import cli
    observed = []
    def called():
        observed.append(sys.argv[1:])
        return 7
    monkeypatch.setattr(cli, entry, called)
    assert packaged._dispatch(([switch] if switch else []) + ['argument', 'a b']) == 7
    assert observed == [['argument', 'a b']]


def test_internal_dispatch_allowlist_and_core_arguments(monkeypatch):
    from pbdesk import cli
    observed = []
    monkeypatch.setattr(cli, 'core_main', lambda argv: observed.append(argv) or 9)
    assert packaged._dispatch(['--pbdesk-run-module', 'pbdesk.cli', '--demo']) == 9
    assert observed == [['--demo']]
    for args in (['--pbdesk-run-module'], ['--pbdesk-run-module', 'os'], ['--pbdesk-unknown']):
        with pytest.raises(packaged.PackagedRuntimeError):
            packaged._dispatch(args)


def test_notification_uses_host_environment(monkeypatch):
    import asyncio
    from pbdesk.services import notifications
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/bundle')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    monkeypatch.setattr(notifications.shutil, 'which', lambda _: '/usr/bin/notify-send')
    calls = []
    class Process:
        returncode = 0
        async def wait(self):
            return 0
    async def spawn(*args, **kwargs):
        calls.append(kwargs)
        return Process()
    monkeypatch.setattr(notifications.asyncio, 'create_subprocess_exec', spawn)
    assert asyncio.run(notifications.Notifier(True).notify({'title': 'fixture', 'body': 'fixture'}))
    assert 'LD_LIBRARY_PATH' not in calls[0]['env']


@pytest.mark.parametrize('frozen', [False, True])
def test_install_service_uses_persistent_image_or_source_command(tmp_path, monkeypatch, frozen):
    from types import SimpleNamespace
    from pbdesk import cli
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'executable', '/cache/old-runtime/pbdesk')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('PBDESK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(tmp_path))
    monkeypatch.setenv('APPIMAGE', '/applications/pbdesk.AppImage')
    monkeypatch.delenv('REALAPPIMAGE', raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/cache/old-runtime/_internal')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess, 'run', run)
    cli.admin_main(['install-service'])
    unit = (tmp_path / 'config/systemd/user/pbdesk-core.service').read_text()
    if frozen:
        assert 'ExecStart="/applications/pbdesk.AppImage" "--core" "--foreground" "--standalone"' in unit
        assert '/cache/old-runtime' not in unit
        assert 'LD_LIBRARY_PATH' not in calls[0]['env']
    else:
        assert 'ExecStart="/cache/old-runtime/pbdesk" "-m" "pbdesk.cli"' in unit


@pytest.mark.parametrize('fixture', [None, '/tmp/${HOME}/events.json'])
def test_frozen_transient_launch_uses_cached_command_and_reset(tmp_path, monkeypatch, fixture):
    from types import SimpleNamespace
    from pbdesk.services import lifecycle
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/cache/runtime/pbdesk')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/mount/_internal')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    monkeypatch.delenv('PBDESK_LIFECYCLE', raising=False)
    answers = iter([None, {'ready': True, 'client_lifecycle': 1, 'history_encryption': 1}])
    monkeypatch.setattr(lifecycle, '_probe', lambda *args: next(answers))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', lambda *a, **k: pytest.fail('must use transient service'))
    if fixture:
        import time
        from pbdesk.services import core_settings
        restart = {'instance_id': 'old', 'expires_at': time.time() + 60, 'profile': {
            'notifications': True, 'demo': False, 'fixture': fixture, 'standalone': False, 'timing': {}}}
        monkeypatch.setattr(core_settings, 'pending_restart', lambda *args, **kwargs: restart)
        monkeypatch.setattr(core_settings, 'clear_restart', lambda *args: None)
        monkeypatch.setattr(lifecycle, '_probe', lambda *args: next(answers))
        answers = iter([None, {'ready': True, 'client_lifecycle': 1, 'history_encryption': 1, 'instance_id': 'new'}])
    assert lifecycle.ensure_core(config.Settings(tmp_path))['ready']
    transient = calls[1][0]
    assert '--setenv=PYINSTALLER_RESET_ENVIRONMENT=1' in transient
    assert '--expand-environment=no' in transient
    expected = ['/cache/runtime/pbdesk', '--pbdesk-run-module', 'pbdesk.cli', '--foreground', '--notifications']
    if fixture:
        expected += ['--fixture', fixture]
    assert transient[-len(expected):] == expected
    assert all('LD_LIBRARY_PATH' not in kwargs['env'] for _, kwargs in calls)


@pytest.mark.parametrize('existing', [False, True])
def test_writable_explicit_application_override_reaches_dispatch(tmp_path, monkeypatch, existing):
    root = tmp_path / 'new-parent/history'
    if existing:
        root.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv('PBDESK_APPLICATION_DIR', str(root))
    called = []
    monkeypatch.setattr(packaged, '_dispatch', lambda args: called.append(args) or 7)
    assert packaged.main(['--core']) == 7
    assert called == [['--core']]
    assert root.is_dir() and root.stat().st_uid == os.getuid()
    assert root.stat().st_mode & 0o777 == 0o700
    if not existing:
        assert root.parent.stat().st_mode & 0o777 == 0o700
    assert not list(root.iterdir())


def test_default_missing_application_directory_is_not_created(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'missing-image-parent'
    monkeypatch.delenv('PBDESK_APPLICATION_DIR', raising=False)
    monkeypatch.setattr(config, 'application_directory', lambda: root)
    monkeypatch.setattr(packaged, '_dispatch', lambda _: pytest.fail('must fail before startup'))
    assert packaged.main([]) == 1
    assert not root.exists()
    assert 'writable application directory' in capsys.readouterr().err

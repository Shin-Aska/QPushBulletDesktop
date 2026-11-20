"""Process commands and environment boundaries for source and frozen launches."""
import os
from pathlib import Path
import sys

MODULES = frozenset({'pbdesk.cli', 'pbdesk.web', 'pbdesk.services.core_settings'})


def command(module, *arguments):
    """Build a subprocess command vector for an allowed pbdesk entry-point module.

    :param module: Dotted module name registered in ``MODULES``.
    :type module: str
    :param arguments: Positional CLI arguments passed to the entry-point module.
    :type arguments: object
    :return: Command argument list ready for subprocess execution.
    :rtype: list[str]
    :raises ValueError: If ``module`` is not in ``MODULES``.
    """
    if module not in MODULES:
        raise ValueError('Unsupported pbdesk process entry point')
    switch = '--pbdesk-run-module' if getattr(sys, 'frozen', False) else '-m'
    return [sys.executable, switch, module, *map(str, arguments)]


def system_environment(environment=None):
    """Restore host library lookup without changing the running frozen process.

    PyInstaller documents LD_LIBRARY_PATH_ORIG as the pre-bootloader value.
    Session variables (including D-Bus, Wayland and XDG) are preserved.

    :param environment: Optional base environment mapping; defaults to ``os.environ``.
    :type environment: collections.abc.Mapping[str, str] | None
    :return: Cleaned environment dictionary suitable for launching host tools.
    :rtype: dict[str, str]
    """
    env = dict(os.environ if environment is None else environment)
    if getattr(sys, 'frozen', False):
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original:
            env['LD_LIBRARY_PATH'] = original
        else:
            env.pop('LD_LIBRARY_PATH', None)
        roots = [str(getattr(sys, '_MEIPASS', '')), env.get('APPDIR', '')]
        for key in ('LD_LIBRARY_PATH', 'PATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
            if key in env:
                values = [part for part in env[key].split(os.pathsep)
                          if part and not any(root and (part == root or part.startswith(root + '/')) for root in roots)]
                if values:
                    env[key] = os.pathsep.join(values)
                else:
                    env.pop(key, None)
        env.pop('APPDIR', None)
        for key in list(env):
            if key.startswith('_PYI_'):
                env.pop(key)
    return env


def child_environment(environment=None):
    """A child bootloader must initialize its own persistent onedir runtime.

    :param environment: Optional base environment mapping; defaults to ``os.environ``.
    :type environment: collections.abc.Mapping[str, str] | None
    :return: Environment dictionary with PyInstaller reset flags applied when frozen.
    :rtype: dict[str, str]
    """
    env = system_environment(environment)
    if getattr(sys, 'frozen', False):
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        # APPDIR names the temporary FUSE mount, never our persistent runtime.
        env.pop('APPDIR', None)
    return env


def appimage_path():
    """Return the resolved path to the active AppImage binary, if running inside one.

    :return: Resolved path to the AppImage file, or ``None`` outside an AppImage.
    :rtype: pathlib.Path | None
    """
    value = os.environ.get('REALAPPIMAGE') or os.environ.get('APPIMAGE')
    return Path(value).expanduser().resolve() if value else None

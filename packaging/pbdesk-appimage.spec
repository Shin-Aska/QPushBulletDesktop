# Exact build dependencies live in requirements-build.lock.
import importlib.metadata
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
runtime = root / 'packaging/requirements-runtime.lock'
names = [line.split('==', 1)[0] for line in runtime.read_text().splitlines()
         if line and not line.startswith(('#', ' ', '\t'))]
datas, binaries, hiddenimports = [], [], []
for package in ('pbdesk', 'sqlcipher3'):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for package in ('sqlalchemy.dialects.sqlite', 'keyring.backends', 'uvicorn.protocols',
                'uvicorn.loops', 'uvicorn.lifespan', 'mcp', 'alembic'):
    hiddenimports += collect_submodules(package)
hiddenimports += ['PyQt6.QtSvg', 'PyQt6.QtDBus', 'PyQt6.QtNetwork']
# importlib.metadata is used by diagnostics, Alembic and backend discovery.
for name in ['pbdesk'] + names:
    datas += copy_metadata(name)
# Include the actual wheel license texts; the build record also reports gaps.
datas += [(str(root / 'LICENSE'), 'licenses/pbdesk'),
          (str(root / 'PRIVACY.md'), 'licenses/pbdesk'),
          (str(root / 'CHANGELOG.md'), 'licenses/pbdesk')]
a = Analysis([str(root / 'packaging/entrypoint.py')], pathex=[],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'selenium', 'build', 'pip', 'PyInstaller', 'tkinter'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='pbdesk',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='pbdesk')

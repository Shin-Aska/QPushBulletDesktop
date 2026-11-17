# Rebuild and replace bundled libraries

pbdesk is GPL-3.0-only. Qt/PyQt and other components retain the licenses supplied in the AppImage's `_internal/licenses` tree and the matching license/source assets. Qt is dynamically linked; this distribution imposes no prohibition on modifying, reverse engineering for debugging modifications, rebuilding, or replacing those libraries. There is no signature or secret needed to run a rebuilt image. The runtime manifest is an integrity inventory that you can regenerate from your own modified files.

## Matching material and scope

The release supplies the application's actual source/build inputs, the `corresponding-source.json` ledger, source tar parts, actual license texts, and original upstream SBOMs. Extract every corresponding-source tar part into the same directory. The files are original upstream or Ubuntu source archives; SHA256 values and exact Ubuntu source package versions are in the ledger. Ubuntu `.dsc`, original archives and Debian archives include the downstream patches and `debian/rules`; they are not just a link to an unpatched upstream release. Use `dpkg-source -x <package>.dsc` to apply those patches, then follow that package's build instructions. Compiler/development tools and normal system build libraries must be installed deliberately in your own build environment.

Source archives cover the application and applicable GPL/LGPL/MPL library obligations. Permissive components retain actual copyright/license notices, immutable package/source identities where available, and source references. Source archives downloaded solely to obtain their notices need not all be republished. GCC runtime libraries are accompanied by their copyright and GCC Runtime Library Exception text. Zstd uses its BSD option. The ledger states the official AppImage runtime's unresolved *permissive* Alpine static-package revisions; this distribution does not claim identical compiler output or bit-for-bit reproducibility.

## Build the application

The source archive contains `pyproject.toml`, hash-pinned Python/build requirements, the exact PyInstaller spec, `AppRun`, bootstrap code, assets and migrations. Historical `design/` screenshot galleries are omitted because they are not compilation or relinking inputs. The public tagged repository retains them. Follow `.github/workflows/appimage.yml` for the recorded Ubuntu 24.04 / CPython 3.14.4 build environment. Production and test environments remain separate. The normal release builder runs on temporary GitHub Actions runners; publishing this recipe does not install tools on an existing user's desktop.

To experiment with a local modified *unpacked* runtime, extract the AppImage using its `--appimage-extract` option. Work on a copy, with synthetic data and explicit application/state directories. Rebuild the desired ABI-compatible library and replace the corresponding regular file below `squashfs-root/usr/pbdesk/_internal`. Qt's libraries and plugins are below `PyQt6/Qt6`. Do not edit a shared cache used by a running core or desktop.

For Qt 6.12.0, the provided `qtbase`, `qtsvg`, `qtwayland`, `qtimageformats` and `qttranslations` source archives contain the relevant modules and bundled third-party attribution/build files. Build QtBase into a writable prefix with its CMake `configure`/`cmake --build`/`cmake --install` instructions, then configure the remaining modules against that prefix with `qt-configure-module`. Build the supplied translation `.ts` sources into replacement `.qm` resources with Qt Linguist tools as documented by qttranslations. Retain the modules/plugins/translations needed by the spec. These are standard upstream source builds, not a promise to reproduce Riverbank's undisclosed wheel compiler flags. The PyQt6 source distribution contains its SIP interface definitions and `pyproject.toml`; build it against the modified Qt prefix with the documented SIP/pyqt-builder backend. PyQt6-sip source is included as well. Version-compatible replacement preserves the existing dynamic interfaces; an ABI change may require rebuilding PyQt and pbdesk together.

Regenerate the inventory with the supplied standard-library helper:

```python
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('pbdesk_build', 'packaging/build_appimage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.runtime_manifest(Path('squashfs-root/usr/pbdesk'))
```

Run `squashfs-root/AppRun` with an explicit writable `PBDESK_APPLICATION_DIR` and private `PBDESK_STATE_DIR`, or repack the AppDir with `mksquashfs` and concatenate it after the runtime as `packaging/build_appimage.py` does. A changed manifest produces a separate runtime cache identity. Use your own artifact name/checksums; the original release evidence applies only to the unmodified release.

## Relink the static AppImage launcher

The outer launcher is the unmodified official AppImage type2 runtime at the commit recorded in `source-policy.json`. Its source archive includes `src/runtime/runtime.c`, `Makefile`, `data_sections.ld`, and the inspected upstream Docker/chroot recipes. The separately supplied libfuse 3.15.0 source must have `patches/libfuse/mount.c.diff` from that runtime applied before building the static library. Squashfuse 0.5.2 source is also supplied. Upstream's recipe records both source checksums, the FUSE patch and static linker flags.

Use that recipe in an Alpine 3.21 build environment to build a modified libfuse and the static squashfuse prerequisites, then rebuild `src/runtime/Makefile` against them. The runtime's `scripts/build-runtime.sh` documents section placement, stripping and the `AI\x02` bytes at offset 8. No application object is withheld: the launcher itself is supplied as C source; normal system/compiler libraries are available from the documented Alpine environment. Its MIT and third-party notice texts are supplied. Replacing the launcher requires rebuilding the complete outer AppImage and rerunning its extraction/bootstrap/process tests, even when the inner Python runtime manifest is unchanged.

The archived upstream recipes are reference material, not instructions executed while downloading source for this release. Review them before running a build of your modifications. The provided Qt source and relinking freedoms are also described by [Qt's open-source obligations](https://www.qt.io/development/open-source-lgpl-obligations).

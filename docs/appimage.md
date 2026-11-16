# Private AppImage candidates

The current package version remains **0.1.0**. Packaging preparation does not create a 1.0 tag, publish a release, or change repository visibility. Candidate builds run on temporary GitHub Actions runners; no local build-tool installation is required or authorized by this workflow.

## Build and retrieve a candidate

The [AppImage workflow](../.github/workflows/appimage.yml) runs when a `build/**` branch is pushed. Once the workflow is present on the default branch, it can also be run with **Actions → Private AppImage candidate → Run workflow**, selecting the reviewed revision. Candidates require the repository to remain private. There are no PR/fork triggers, and the candidate job has only `contents: read`.

The workflow installs OS dependencies only on its temporary Ubuntu 24.04 runner. It verifies the SHA256 of an exact official CPython 3.14.4 archive, seeds its temporary tool cache, and selects it through a commit-pinned `setup-python`. Separate environments run the full repository regression suite and freeze only the production desktop/web/MCP dependencies. Python distributions use exact version and SHA256 pins with `--require-hashes --only-binary=:all:`. SQLCipher must come from the pinned binary wheel; the workflow does not substitute Ubuntu's SQLCipher development package or build a different encryption engine.

PyInstaller 6.22.3 builds a standalone directory. The AppDir contains that executable/runtime, application resources and metadata, Qt plugins/libraries, migrations, SQLCipher and its native dependencies. The builder verifies an exact official AppImage type-2 runtime and appends a SquashFS filesystem using the runner's recorded `squashfs-tools` version. Tool URLs and hashes live in [toolchain.json](../packaging/toolchain.json); resolved Python and OS package versions are recorded in each build. The pinned release inputs and captured runner inventory improve auditability; the mutable Ubuntu apt repositories and hosted runner image mean this is not a claim of bit-for-bit reproducible builds.

A successful run uploads one private artifact named `pbdesk-appimage-<commit>`, retained for **two days**. Failure evidence is retained for one day. Download the artifact before it expires; repeated large candidates count toward the account's Actions storage quota. This workflow creates no cache and enables no paid usage. The bundle contains:

- `pbdesk-0.1.0-x86_64.AppImage`
- `pbdesk-0.1.0-source.tar.gz`, containing the actual selected working-tree source/build inputs, with private profiles excluded
- `pbdesk-0.1.0-build-info.json`, including source revision/status, input hashes, dependency/tool inventory, runtime identity and compliance blockers
- `pbdesk-0.1.0-licenses.tar.gz`, containing actual available wheel/CPython/project license texts and source accounting
- `smoke-evidence.json` and `pbdesk-0.1.0-SHA256SUMS`

Verify the downloaded files with `sha256sum --check pbdesk-0.1.0-SHA256SUMS`. The source archive uses the bytes actually frozen, including tracked modifications and nonignored new inputs; it does not blindly archive an older commit. The workflow's clean checkout ordinarily binds those inputs to the recorded commit. A private candidate artifact and the license inventory are not claims that all final-distribution legal obligations are already satisfied.

## Launch and persistent processes

Mark the AppImage executable and launch it directly. FUSE-capable desktop sessions use the normal type-2 mount; `APPIMAGE_EXTRACT_AND_RUN=1 ./pbdesk-0.1.0-x86_64.AppImage` is the supported extraction fallback when FUSE is unavailable. Launching downloads and installs nothing.

`AppRun` invokes the frozen executable's bootstrap. Before application dispatch, it validates the bundled manifest and copies the complete runtime into a private `0700` cache below `$XDG_CACHE_HOME/pbdesk/runtimes/<runtime-id>` (normally `~/.cache/pbdesk/runtimes/`). It validates file SHA256 values/modes and in-tree symlink targets, then executes the cached runtime. Core, optional web host, restart helpers and later desktops use this surviving runtime after the original mount disappears. Existing runtimes are deliberately retained because another process may still use them; do not remove a runtime directory while pbdesk services are running. Replacing the AppImage starts a separately identified runtime.

The application directory comes from the real outer AppImage path (`REALAPPIMAGE` when supplied by an integration, otherwise `APPIMAGE`), rather than its mounted or cached interpreter directory. The default database remains `pbdesk.sqlite3` beside that file. The containing directory must be writable. Explicit `PBDESK_APPLICATION_DIR` and `PBDESK_STATE_DIR` overrides retain their normal meaning. Bootstrap state and credential references stay in the private state directory. Do not place candidate executables beside the owner's real database for smoke testing.

The frozen executable supports normal desktop arguments and these dispatch selectors: `--core`, `--web`, `--mcp`, `--admin`, `--diagnostics`. `--admin install-service` is an explicit operation and writes a persistent original-AppImage command; candidate builds do not install or start a user service.

## Verification and remaining release gates

CI runs source regression tests first, then the actual AppImage from outside the source checkout with `PYTHONPATH` and `PYTHONHOME` removed. [The external smoke harness](../packaging/smoke.py) uses synthetic temporary profiles, its own private session bus and fake KWallet, and an isolated Xvfb display. It exercises the bundled SQLCipher/FTS5, first-run desktop and packaged process behavior, and records exactly which checks completed. It never uses the owner's live profile, wallet, providers or systemd services. A successful workflow is necessary evidence; source/helper tests alone do not establish that an AppImage builds or runs.

Ubuntu/Xvfb evidence does not certify native KDE Plasma 6 Wayland, real KWallet authorization, notification/tray integration, suspend/resume, or existing-profile upgrades on Kubuntu 26.04. Those remain explicit supported-desktop acceptance tasks in [the release plan](release-plan.md).

Final distribution is **blocked** by [release-readiness.json](../packaging/release-readiness.json) and candidate build accounting. Existing wheel license texts and URLs do not replace applicable GPL/LGPL/native corresponding-source obligations. Review and complete matching third-party source/build material, native-library provenance and notices, and supported-desktop/upgrade evidence before changing these guards.

The final path requires a deliberate stable `v1.0.0` or later tag exactly matching the package version, a deliberately public repository, cleared readiness checks, and an artifact explicitly approved for final distribution. Only that separate job receives `contents: write`, and it creates an owner-reviewed **draft** release. Current 0.1.0 candidates cannot pass those guards. There is no automatic public release, visibility change or shadow-drop trigger.

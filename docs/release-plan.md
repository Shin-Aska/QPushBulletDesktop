# First public release

Target: a stable **1.0.0** shadow drop. Candidate builds and validation happen privately before publishing the first final release. The private candidate workflow and build instructions are in [Private AppImage candidates](appimage.md). A successful CI build and packaged smoke record are required before claiming artifact validation; source tests alone are insufficient.

## Agreed delivery

- Distribute an AppImage through GitHub Releases, built by GitHub Actions.
- Bundle the application runtime so ordinary users do not need an IDE, source checkout, Python environment, or development commands to launch it.
- Keep the initial supported desktop target aligned with the current implementation: Kubuntu 26.04, KDE Plasma 6, and Wayland. Broader Linux compatibility needs its own evidence.
- Preserve the approved Device relay artwork in the application, window, tray, and launcher metadata.
- Treat the owner's current daily use as real-world acceptance evidence. Focus remaining regression and reliability checks on the actual packaged artifact and changes to installation, process launching, and upgrades.
- The selected project license is **GPL-3.0-only**, consistent with the current PyQt6 dependency. Keep third-party notices and applicable corresponding-source material with the distribution. Pushbullet's service terms are separate. Version 1.0.0 remains the public release target; packaging preparation does not itself publish it.

## Direct launch for the current checkout

`./launch-pbdesk` uses the existing `.venv`, source tree, `.runtime/editor` state, project-root database, and ports 18765/18766. It matches the configured editor tasks without opening the editor. Explicit `PBDESK_*` environment values remain supported. It installs nothing and does not move, copy, or migrate the existing profile.

This launcher is for the configured source checkout. It is separate from the planned self-contained AppImage.

## AppImage build and process requirements

1. Bundle CPython, the application, required Python dependencies including the pinned SQLCipher driver and its native runtime, Qt libraries/plugins, application resources, and the applicable third-party notices. Development tools and private `.runtime`, database, wallet, and editor files must not enter the artifact.
2. Resolve the real AppImage file's directory explicitly. The existing requested layout keeps `pbdesk.sqlite3` beside the application executable; it must not resolve to the read-only mounted AppImage filesystem or the Python interpreter directory. Give a useful error if that directory is not writable. Preserve explicit application/state overrides.
3. Keep bootstrap credentials and private runtime state in the configured private state directory. Replacing the AppImage must preserve message history, settings, saved summaries, and credential references.
4. Verify every process entry point: desktop, core, optional web host, restart helper, and supported command-line tools. A surviving core or web process must retain access to its bundled runtime after a desktop exits or another AppImage instance opens. Source-launch assumptions about `sys.executable -m ...` need explicit packaged-runtime handling.
5. Preserve KWallet/session D-Bus access, native Wayland and fallback platform loading, tray behavior, notification delivery, and optional systemd-user integration. These are runtime integration requirements; an archive that merely builds is not sufficient evidence.
6. Keep launch offline with respect to installation: starting the application must not download Python, packages, or build tooling. Normal configured Pushbullet/OpenAI operations remain as documented in the privacy policy.

## GitHub Actions and Releases

The implemented candidate path runs on `main` and `build/**` branch pushes or manual dispatch in the private repository, with two-day artifact retention and read-only repository permissions. It builds the actual AppImage, runs packaged smoke checks, then requires the full source regression suite and checksum verification before uploading a successful candidate. All build-tool and OS dependency installation happens on temporary GitHub runners. The package stays at 0.1.0. Final drafting is fail-closed while `packaging/release-readiness.json` and the artifact source accounting identify incomplete corresponding-source, native-license and supported-desktop acceptance work. It requires an exact deliberate stable version tag, a separately public repository and a final-ready artifact; only its separate draft-release job receives write permission. Nothing in candidate CI changes visibility or publishes a public release.

The pipeline should produce candidate artifacts for explicit validation and final artifacts from a deliberate version tag. It must run the relevant regression suite, build from the selected source revision, and smoke-test the actual AppImage independently of the source checkout. Use pinned tooling and preserve the runtime/dependency versions in the build record.

Final release assets should include the AppImage, checksums, matching source/build information, privacy policy, changelog/release notes, and the chosen project and third-party license material. Build permissions should remain read-only where possible; publishing permissions belong to the release job. Final publication is a separate deliberate release operation.

## Acceptance before publishing

- Launch the packaged application directly on a clean supported desktop, without an IDE or a separately installed Python environment.
- Verify that a new profile completes **Protect your local history**, existing-KWallet authorization, and an explicit recovery backup/decline before account connection. Open an established profile only through the explicit verified migration/unlock flow, preserving the chosen database directory and credential store. See [encrypted history](encrypted-history.md).
- Verify SQLCipher availability, encrypted database/WAL behavior, migration interruption/resume, retained-ancestor conflict detection, missing or wrong keys, recovery-key validation, and fresh wallet-entry recovery in the actual packaged runtime. Inventory historical plaintext copies separately; migration is not a recursive backup-erasure feature.
- Exercise ordinary Notifications, SMS, summary, media, tray, and Settings workflows against the packaged runtime; retain the owner's daily-use feedback alongside any targeted checks.
- Verify that another desktop or enabled web host survives the first desktop's exit, and that core restart and Stop all services still work with the bundled executable.
- Replace the AppImage with a candidate update and verify retained history, settings, and credential references. Exercise startup errors, lost connectivity, locked wallets, and suspend/resume where the packaging changes can affect them.
- Record the full regression result and distinguish real-provider, mocked-provider, skipped, and untested behavior. Any additional test sends or paid AI validation require the usual explicit content/destination or usage authorization.
- Include the selected GPL-3.0-only license and applicable third-party notices/corresponding-source material, then review the privacy policy and changelog against the final artifact before publishing 1.0.0.

The encryption implementation and its isolated-fixture checks do not mean the owner’s live profile is already encrypted. Its older running core must be deliberately closed/restarted before the first protection dialog can activate this revision; no such live activation was performed by this work.

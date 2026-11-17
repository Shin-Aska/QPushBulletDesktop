# QPushBulletDesktop 1.0.0

The first stable Linux release of QPushBulletDesktop (`pbdesk`): a native Qt inbox for Pushbullet, with encrypted local history and optional AI assistance.

- Browse and search pushes, mirrored notifications and SMS. SMS starts in one All devices view; device labels and the phone filter keep each phone's conversations distinct.
- Compose notes, links and SMS through explicit destination/content review and approval. Saving or generating a draft sends nothing.
- Manage devices and local decryption references, view message activity, and use the tray, native appearance and keyboard controls.
- Protect local history, preferences and saved summaries with SQLCipher. KWallet holds the dedicated key; recovery backup and explicit plaintext migration are built in.
- Optionally use OpenAI for saved summaries and editable SMS reply drafts with reviewed local context.
- Run the self-contained AppImage without installing Python, Qt, an IDE or application packages.

## Install and upgrade

Download `pbdesk-1.0.0-x86_64.AppImage` and the matching checksum file. Verify the downloaded AppImage with `sha256sum --check --ignore-missing pbdesk-1.0.0-SHA256SUMS` and require its line to report **OK**, then make the AppImage executable, and place it in a writable application folder. The checksum file covers all listed release assets. To verify a complete downloaded bundle, keep every listed file together and use `sha256sum --check pbdesk-1.0.0-SHA256SUMS` without `--ignore-missing`. Launch downloads and installs nothing. FUSE is used where available; `APPIMAGE_EXTRACT_AND_RUN=1 ./pbdesk-1.0.0-x86_64.AppImage` supports extraction-based launch.

The default database is `pbdesk.sqlite3` beside the AppImage. Replace the executable in the same folder to retain that profile. Keep the private XDG state directory and KWallet entries. Existing plaintext history requires the explicit verified migration flow; missing keys cannot fall back to plaintext. Save the offered recovery key separately from the database, or explicitly decline the backup. Read the [encrypted-history guide](https://github.com/Shin-Aska/QPushBulletDesktop/blob/v1.0.0/docs/encrypted-history.md).

The supported desktop target is Kubuntu 26.04, KDE Plasma 6, Wayland and KWallet. Owner feedback supplies basic native launch/product acceptance. Automated artifact checks use isolated Linux/Xvfb profiles and a fake wallet; their exact source/artifact identities, counts and limits are recorded in the shipped smoke, regression, upgrade/recovery/migration and readiness evidence. They do not claim exhaustive real-wallet, tray, notification, suspend/resume or every native Wayland integration test. Other platforms remain outside the supported target.

## Licenses, source and modified libraries

pbdesk and PyQt6 use GPLv3; Qt libraries use LGPLv3 and retain their third-party notices. Other bundled components retain their respective licenses. This release supplies actual license texts, a component/source ledger, applicable corresponding-source tar parts (including matching Qt modules/translations and Ubuntu downstream patches), and the application source/build inputs. Source downloads are available beside the executable, without an extra charge.

Extract all corresponding-source tar parts together. Follow the supplied [rebuild and library-replacement instructions](https://github.com/Shin-Aska/QPushBulletDesktop/blob/v1.0.0/packaging/REBUILDING.md) to build modified Qt/PyQt or system libraries, regenerate the editable integrity manifest, and repackage or run a modified AppDir. The static launcher's libfuse source, patch, runtime code and relinking recipe are supplied as well. No secret, upstream signature or identical compiler output is required. The ledger explicitly records the upstream launcher's unresolved permissive Alpine package revisions; bit-for-bit reproducibility is not claimed.

Pushbullet credentials, incoming decryption references and OpenAI keys remain in the configured credential store. Configured automatic summaries may send eligible cached text to OpenAI in the background. Review the [privacy policy](https://github.com/Shin-Aska/QPushBulletDesktop/blob/v1.0.0/PRIVACY.md), summary scope and SMS context before enabling AI.

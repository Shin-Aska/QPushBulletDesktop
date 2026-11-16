# QPushBulletDesktop

QPushBulletDesktop is a Qt desktop app for Pushbullet, with optional AI features. Linux is the only supported platform at the moment. The application and command-line executable are named `pbdesk`.

[AppImage candidates](https://github.com/Shin-Aska/QPushBulletDesktop/actions/workflows/appimage.yml) · [Build and installation guide](docs/appimage.md) · [Privacy](PRIVACY.md) · [Changelog](CHANGELOG.md) · [Release plan](docs/release-plan.md)

## What it does

- Read and search Pushbullet pushes and mirrored notifications in a native desktop inbox.
- Browse SMS in one **All devices** view. Conversations stay separate by phone and show their device labels; filter the list by phone when needed.
- Review and explicitly approve Note, Link, and SMS actions. SMS requires choosing a sending phone and recipient. Saving a draft or pending action does not send it; the review shows the destination and content before approval.
- Register and manage devices, inspect their capabilities, and configure local decryption references.
- Optionally create saved summaries and editable SMS reply drafts with OpenAI. Summary presets control their sources and scope; SMS drafts can use recent cached conversation context and a selected tone. AI is never required for inbox or SMS features, and drafts are never sent automatically.
- Use the system, light, or dark Qt appearance and keep the app available from the desktop tray.
- Keep local history encrypted with SQLCipher, using KWallet for the database key and a recovery key for backup or recovery.

The optional local web and MCP interfaces are intended for advanced users; see [architecture and capabilities](docs/architecture.md) and [configuration](docs/configuration.md).

## Install on Linux

The repository is public, but it has no tagged 1.0 release yet. AppImages are built by GitHub Actions; open the [AppImage workflow](https://github.com/Shin-Aska/QPushBulletDesktop/actions/workflows/appimage.yml), choose a successful run, and download its candidate artifact. Candidate availability depends on a successful workflow run. Build and launch details are in the [AppImage guide](docs/appimage.md).

The AppImage bundles Python, Qt, and SQLCipher, so end users do not need Python or an IDE. Put it in a writable folder and launch it there. Keep the database and settings beside the AppImage; when upgrading, replace the AppImage in the same folder to keep using that local profile. On first launch, follow **Protect your local history**: authorize KWallet and save the offered recovery key somewhere safe, or explicitly continue without a backup. The recovery key and database together can expose your local history.

The current tested desktop setup is Kubuntu 26.04 with KDE Plasma 6 on Wayland and KWallet. Other Linux desktop and distribution combinations have not been certified. Windows and macOS are not currently supported.

## Connect Pushbullet and phones

Connect an existing Pushbullet account in **Settings → Account** with an access token. On each Android phone you want to use, install and sign in to the Pushbullet app with the same account, then allow its SMS and notification permissions. Registered devices and their available capabilities appear in **Settings → Devices**. SMS sending requires an active SMS-capable phone; history-only phones remain available for reading.

Set up optional AI in **Settings → AI** with your own OpenAI API key. When an AI feature is enabled or invoked, the selected message content or conversation context is sent to OpenAI to produce that result. Review summary scope and SMS context before use. An AI-generated SMS reply remains an editable draft and requires the usual explicit review and approval to send. See the [privacy policy](PRIVACY.md) for data handling details.

## Learn more

- [Encrypted history, migration, and recovery](docs/encrypted-history.md)
- [AppImage build and launch details](docs/appimage.md)
- [Capabilities and feature boundaries](docs/capabilities.md)
- [Changelog](CHANGELOG.md) and [release plan](docs/release-plan.md)

QPushBulletDesktop is an independent Pushbullet client, licensed under [GPL-3.0-only](LICENSE).

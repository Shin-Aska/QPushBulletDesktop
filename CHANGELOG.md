# Changelog

User-visible changes for pbdesk are recorded here. Dates are assigned when releases are published.

## Unreleased

Target: the first public stable release, **1.0.0**. No release has been tagged or published by this entry.

### Added

- Mandatory SQLCipher encryption for local history, settings, saved summaries, and AI runtime state, with protection/unlock before account setup and no plaintext fallback.
- A dedicated random database key in an existing KWallet, separate from provider, incoming decryption, and AI credentials; explicit recovery-key backup or decline, plus verified recovery into a fresh wallet entry.
- Explicit, verified plaintext migration with encrypted rollback archives, resumable migration state, and guarded cleanup of selected legacy files. Unrelated historical backups are preserved and may remain plaintext.

- Native Linux desktop for Pushbullet, with Summary, Notifications, SMS, and reusable Settings pages.
- Notification search, source-device filtering, complete supplied message text, selectable content, and explicitly activated web links.
- Locally cached SMS conversations with incoming and outgoing messages, independent phone selection, conversation search, and an editable composer.
- Explicit note, link, and SMS composition through saved requests, exact review, and separate sending approval.
- Message activity showing pending, accepted, failed, uncertain, expired, and cancelled requests.
- Device registration, rename, and removal controls, with local history preserved after removal.
- Incoming end-to-end decryption using local shared or device-specific KWallet references, plus recoverable encrypted records when a key is unavailable.
- Notification application icons, contact thumbnails, and explicitly loaded native previews for remote images and supported image attachments.
- Automatic AI summaries with configurable presets, saved history, search, pins, retention controls, and available usage details.
- Editable AI SMS reply drafts with checked message context and selectable writing tones.
- System, Light, and Dark appearance settings, remembered startup page, keyboard shortcuts, and a system-tray menu.
- First-run account connection in Settings, with provider secrets kept in the operating-system credential store.
- Core status, editable desktop-notification preference, guarded core restart, and Stop all services controls.
- Optional local web status page and local MCP tools for supported read and pending-action workflows.
- Device relay artwork for application branding, windows, dialogs, and the tray, with packaged desktop-launcher assets.
- A direct `launch-pbdesk` command for the configured development checkout, using the same profile as the editor tasks.
- A privacy policy covering local history, provider connections, optional AI processing, retention, and data removal.
- The GNU General Public License, version 3 only (`GPL-3.0-only`), with the license included in package metadata and distributions.

### Changed

- SMS now defaults to one All devices conversation list with device labels and an optional phone filter. Conversations remain separate per phone, including matching contacts or thread IDs; selection and polling read the local cache, and provider synchronization remains an explicit refresh.
- A failed per-phone local SMS read preserves that phone's cached messages while other phones update. Account changes clear SMS caches and reset the filter to All devices.
- Non-secret provider, AI, and interface settings now share the existing SQLite store. Selected legacy JSON settings are imported, verified and archived in encrypted migration material before their plaintext originals are removed; they do not remain a second settings authority.
- Desktop and web-server processes share a core through explicit client leases. The core can stop after the final client leaves, recover after a crash, and respect Stop all services across older clients.
- SMS reply assistance keeps generated text editable and inserts it into the current composer before the ordinary review-and-send flow.

### Fixed

- Accepted SMS requests no longer remain duplicated as local request cards in a conversation; their outcomes remain available in Message activity.
- Unavailable upstream SMS threads no longer prevent available conversations from synchronizing; retained local text remains readable.
- Contact-image loading supports the verified additional Pushbullet image host, with corrected avatar retry state and memory caching for small contact images.
- Native themes, selection states, text wrapping, status reporting, and narrow-window layouts have received consistency improvements.

### Release preparation

- Unified SMS source tests use isolated fake-core fixtures. This entry does not certify a rebuilt AppImage or a new live-profile validation.
- Encryption has been developed against isolated fixtures. The existing live profile and running older core have not been migrated or restarted by this change; activation requires the explicit protection flow. See [encrypted history](docs/encrypted-history.md).

- The selected public distribution target is an **AppImage built through GitHub Actions and distributed through GitHub Releases**. The candidate pipeline and standalone packaged runtime are implemented. In public or private repositories, `main`/build-branch pushes and manual runs build the AppImage and require packaged smoke checks, full regression tests and verified checksums before successful candidate upload. No 1.0 release has been published. Native supported-desktop acceptance and complete third-party license/corresponding-source material remain release gates. See [AppImage candidates](docs/appimage.md).
- The project uses `GPL-3.0-only`. Bundled components retain their applicable licenses, notices, and corresponding-source requirements.
- See [the release plan](docs/release-plan.md) for the agreed packaging target and remaining acceptance checks, and [feature parity](docs/feature-parity.md) for current feature boundaries.

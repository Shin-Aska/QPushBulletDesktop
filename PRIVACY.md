# pbdesk privacy policy

Last updated: 17 November 2026.

pbdesk is an independent Linux desktop client for Pushbullet. This policy describes the current application, its local storage, and the external services it contacts. It does not replace the policies of Pushbullet, OpenAI, your mobile carrier, or your operating system.

## What stays on your computer

pbdesk stores its working history and settings in a local SQLCipher-encrypted SQLite database after protection setup. Depending on the features you use, this includes:

- Pushbullet messages, mirrored notification titles and bodies, timestamps, source applications, device associations, links, and supplied icon metadata.
- SMS message text, direction, timestamps, phone numbers, contact names, conversation identifiers, and supplied thumbnail or attachment references.
- Device registrations and capabilities, synchronization state, and encrypted records retained for later decryption or recovery.
- Saved outgoing requests, their destinations and content, approval state, and submission outcomes.
- Summary presets, generated summaries, saved history, pins, and available usage and timing information.
- Account connection references, appearance preferences, and other non-secret settings.

The database belongs to the configured application directory. The current development launcher uses the checkout's `pbdesk.sqlite3`. Private runtime state normally uses `$XDG_STATE_HOME/pbdesk`, or `~/.local/state/pbdesk` when that variable is unset; development profiles can select a different directory. `PBDESK_APPLICATION_DIR` and `PBDESK_STATE_DIR` explicitly override these locations. Settings → Core shows the active storage locations.

**Local-history encryption is mandatory, but existing plaintext installations are protected only after explicit successful migration.** The desktop requires protection or unlock before showing account setup, history, settings, or AI features. A missing SQLCipher driver, unavailable key, or conflicting database stops access; there is no plaintext runtime fallback.

The dedicated database key is generated randomly after you authorize an existing KWallet. It is separate from the Pushbullet token, incoming end-to-end password, and OpenAI API key. Only opaque database/key-entry identifiers and setup/migration state are kept in the history bootstrap records outside the encrypted database. Service authentication and process-coordination files remain separate private runtime files. Database pages and SQLCipher WAL content are encrypted; SQL temporary storage is kept in memory.

Migration verifies encrypted copies of the selected database, any verified retained ancestor, and known legacy settings/AI cache files before removing the affected plaintext originals. It retains encrypted rollback archives. It does not recursively find or erase historical backups: older out-of-profile `.runtime` snapshots, copied databases, external backups, and storage remnants can still contain plaintext. Your account permissions, disk encryption, and backup practices remain relevant to these copies.

A recovery key can be saved explicitly to a user-chosen private file, or you can explicitly decline the backup. **Anyone with the recovery key and database can read the history.** Keep the recovery key separately from the database, preferably outside this computer. Without a usable KWallet entry or the matching recovery key, pbdesk cannot recover encrypted history. Encryption at rest does not protect history from a compromised account or process while it is unlocked, nor does it erase content already disclosed to other applications or services. See [encrypted history and recovery](docs/encrypted-history.md).

Pushbullet tokens, incoming decryption passwords, the dedicated database key, and the dedicated OpenAI API key are kept in the configured supported operating-system credential store, such as KWallet. SQLite stores references to those entries rather than their secret values. The optional recovery-key export is a separate secret file that you choose to create.

Unsaved SMS drafts remain in the desktop process and are lost when that process exits. Loaded remote images and contact-image previews use bounded memory caches, with no separate image-file cache created by pbdesk. Supplied inline thumbnails and image references can remain in SQLite as part of message metadata.

## Pushbullet and message delivery

After you connect an account, pbdesk contacts Pushbullet's API and event stream to authenticate, synchronize available messages and devices, and receive updates. Explicit device-management and approved sending actions also contact Pushbullet. These requests disclose the information needed for the operation, including your access token and the selected action's content and destination.

Outgoing notes, links, and SMS use the application's explicit plaintext-provider transport. Pushbullet can process that content, and SMS delivery also involves the selected phone and mobile carrier. Incoming end-to-end encryption does not make outgoing SMS end-to-end encrypted.

Remote contact images, MMS images, and image attachments are fetched after you activate the relevant image-loading control. Requests are restricted to supported official HTTPS image origins and do not include the Pushbullet access-token header or cookies. Image hosts still receive normal connection information, such as the requesting IP address and the requested URL.

Pushbullet's handling of its service data is described in its [privacy policy](https://www.pushbullet.com/privacy) and [terms of service](https://www.pushbullet.com/tos).

## Optional OpenAI features

AI features require a dedicated OpenAI API key that you configure locally. Requests go directly from the local core to OpenAI; pbdesk does not route them through a maintainer-operated AI server.

Once AI is configured and an account is available, **automatic summaries can send eligible cached notification and SMS text to OpenAI in the background, including at application startup**. The active summary preset determines the sources, time window, devices, and conversations considered. Summary input can include message text, notification titles, application or conversation labels, timestamps, and message direction.

For SMS reply assistance, you choose context from up to the latest 20 cached messages in the selected conversation. An explicit draft-generation action sends the checked messages' text, timestamps and direction, together with the writing-tone preference. Opening the local context preview does not generate a reply. Generated replies remain editable and require the normal separate sending approval.

pbdesk applies limited text cleanup before AI requests, including removal of recognizable links, email addresses, and some credential patterns. **This is not anonymization:** names, phone numbers, sensitive details, and other personal information can remain in message text. Review the context and summary scope accordingly. AI requests contain text rather than image attachments.

Generation requests set `store: false`. This does not guarantee zero provider retention. OpenAI's account settings and [API data controls](https://developers.openai.com/api/docs/guides/your-data) govern provider-side handling, including abuse-monitoring retention. Model-list requests also contact OpenAI using your dedicated key. AI usage may incur charges on that account.

## Other local applications and external links

When desktop notifications are enabled, pbdesk passes notification titles and shortened bodies to the operating system's notification service. Your desktop's notification history and lock-screen settings can affect who sees that content and how long it remains visible.

The optional web host binds to local loopback and currently provides service status rather than an inbox. It does not expose message history in the browser. The local MCP interface can disclose the information returned by its supported tools to the application you connect to it, including notification search results and individual items. It can create pending actions but cannot approve or send them itself. The connected application's own data handling also applies.

Activating a message link opens it in your configured external browser. The destination website and your browser then apply their own policies. Visiting the project's GitHub repository, downloading release files, or submitting an issue likewise involves GitHub under its own policies.

## Telemetry, diagnostics, and support

The application does not implement usage analytics, advertising identifiers, automatic crash uploads, or an automatic update-check service. Normal synchronization and configured AI requests are the external processing described above, not maintainer telemetry. pbdesk does not operate a central server that receives your local message history.

Runtime logs and the diagnostics command can contain technical information such as dependency versions, local paths, credential-entry references, service state, and error details. They are not automatically submitted to maintainers. Review anything you choose to share; do not post message databases, private runtime files, access tokens, API keys, passwords, or unredacted personal content in public issues.

## Retention and removal

Notification history, SMS history, saved outgoing actions, and their local metadata remain on disk; the application currently has no general automatic age-based deletion policy for those records. Removing a device registration preserves its local history.

Saved summary history defaults to a 30-day retention policy, with selectable periods of 7, 30, 90, or 365 days. Pins protect entries from ordinary pruning. Unreferenced generated summaries are removed when their final history reference is removed. Presets, other message history, retained import files, and external backups have separate lifetimes.

Closing the window may leave pbdesk running in the tray. Exit desktop releases that client's use of the core; another desktop or enabled web-host process can keep the core running. Use Stop all services when you intend to stop background processing across these clients.

Removing the application executable does not erase its database, state directory, legacy import files, backups, or credential-store entries. To remove local data, first stop all services, identify the active database and state paths, and remove the relevant files, encrypted migration archives, recovery-key exports, and any copies you have retained. Credential-store entries must be managed separately. Local deletion does not erase data already held by Pushbullet, OpenAI, a carrier, a connected MCP application, or a backup service; use those services' controls for their copies.

## Questions and changes

Privacy questions can be raised in the [project's issue tracker](https://github.com/Shin-Aska/QPushBulletDesktop/issues) without including private message content or credentials. This policy is maintained with the source code and will be updated when the application's data handling changes.

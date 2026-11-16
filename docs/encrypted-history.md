# Encrypted local history

Local-history encryption is mandatory in this source revision. The currently running older core, project-root database and `.runtime/editor` profile were not migrated or restarted by this implementation work. An older running core must be deliberately closed/restarted to activate the new desktop protection flow. This guide describes that flow, not completed encryption of the owner's current data.

## First start and ordinary unlock

1. The desktop opens **Protect your local history** before Pushbullet connection or the main application. Choose **Protect local history**.
2. Unlock and authorize your existing KWallet. pbdesk creates a dedicated random 32-byte database key in the `pbdesk-history` folder, under a fresh `history-UUID` entry. It does not create a wallet or overwrite a credential entry. The database key is separate from the Pushbullet token, incoming `e2e-password`, and OpenAI API key. Normally the wallet authorization is the only password interaction required; there is no additional database password to remember.
3. At **Back up your recovery key**, choose **Save recovery key…** and select a safe location, preferably outside this computer, or explicitly choose **Continue without backup**. Saving uses a new private file with mode `0600`; an existing file is not overwritten. Save success or explicit decline completes this step.
4. Account connection and normal Notifications, SMS, Settings and AI features then become available. On later starts, **Unlock your local history → Unlock with KWallet** authorizes access to the existing key.

Until history is unlocked and the recovery choice is resolved, the core provides only bootstrap/lifecycle services and the desktop protection routes. Provider connections, outgoing dispatch, history/configuration access, web hosting and AI initialization are gated. Wallet and SQL work run outside the GUI thread. A locked core does not expose history through MCP or web clients.

## Existing plaintext history

The updated desktop shows **Encrypt existing history**, with a paths-only preview of the selected application/state files. Choosing **Encrypt existing history** explicitly authorizes migration; startup does not silently continue with plaintext storage.

Migration takes exclusive ownership, exports to encrypted staging on the same filesystem, and checks the complete logical database and integrity before switching. It preserves schema, rows, sequence state, full-text indexes and SQLite user version. Verified rollback material remains encrypted. A durable migration journal permits a retry after interruption; resolve the reported conflict and choose **Retry encryption** rather than deleting protection or journal files.

If both the application database and a retained state-directory database exist, the latter must match the recorded unchanged legacy ancestor. An unrelated or changed second database produces a conflict and is preserved for reconciliation. The selected source and verified ancestor are archived encrypted before their affected plaintext originals and sidecars are removed.

Known legacy `provider-credential.json`, `ai-settings.json`, `ui-preferences.json`, `ai-digest-cache.json` and `ai-runtime-state.json` files in the selected application/state directories are archived inside encrypted migration material. Configuration is imported into the encrypted database; active AI runtime state also lives there. Their selected plaintext originals are removed only after verification/import succeeds. Existing provider, AI and incoming-content wallet entries remain separate.

The database remains `pbdesk.sqlite3` in the configured application directory beside the actual application launcher. This change does not relocate history to XDG data storage. For the configured source checkout, `./launch-pbdesk` retains the project-root database, `.runtime/editor` state and ports 18765/18766.

Migration inventories only the known selected paths. Older out-of-profile `.runtime` snapshots, copied databases and external backups may still be plaintext and remain untouched for separate handling. Removing a file does not prove erasure of SSD/storage remnants. Keep an inventory of these copies rather than assuming protection setup secured every historical copy.

## Recovery and moving a profile

The recovery key is a secret bundle beginning `pbdesk-history-v1:`. It contains the database identity and its encryption key with a checksum; it is not a password hint. **Anyone with this bundle and the database can read the history.** Keep it separate from database backups and avoid public issues, logs or chat messages.

If KWallet is unavailable or its entry is lost, choose **Use a recovery key**, then paste the key into the masked field or choose **Import recovery key file…**. pbdesk verifies the bundle against the existing encrypted database and its stable identity before writing any wallet entry or protection metadata. After authorizing an existing wallet, recovery creates a fresh dedicated entry. It does not overwrite the previous entry. The database identity and key remain stable, so the matching earlier recovery bundle remains valid. The recovery backup offer appears again.

A copied encrypted database is not sufficient for access by itself. Preserve its opaque `.pbdesk-history.json` bootstrap record and the matching wallet access or recovery bundle. Stop all services before making a filesystem copy so database/WAL state is consistent. Keep encrypted migration archives if you need their rollback/legacy material, and keep recovery secrets separately. Moving an AppImage or database does not move KWallet credentials automatically. The self-contained AppImage and GitHub release pipeline remain planned, not delivered.

Without either a usable wallet key or its matching recovery bundle, pbdesk cannot recover encrypted history. A Pushbullet token, incoming-content password or OpenAI key cannot replace the database key.

## When protection cannot continue

| State | Action |
| --- | --- |
| An older core is running | Deliberately close/restart it before activation. The new client rejects it with an instruction to restart for mandatory encrypted history; it leaves that service unchanged. |
| SQLCipher is unavailable | Use an environment/build containing the required `sqlcipher3==0.6.5` driver, then restart the core. Source dependency installation is a deliberate setup operation, never a startup download. There is no plaintext fallback. |
| KWallet is locked or access is denied | Unlock an existing wallet, allow pbdesk access and retry, or use the matching recovery key. |
| Key or protection reference is missing/wrong | Recover using the matching recovery key. Existing files are preserved; pbdesk does not invent a replacement key or bypass encryption. |
| Migration was interrupted | Resolve the reported condition and use **Retry encryption**. Keep its journal and encrypted rollback files. |
| Conflicting databases or a busy operation | Preserve both histories; close competing owners or reconcile the indicated paths before retrying. Do not delete files to force startup. |

SQLCipher encrypts database pages and WAL content; SQL temporary storage stays in memory. Opaque identity/version/setup records, private service credentials and process-coordination files remain outside the encrypted history. Encryption at rest does not prevent a compromised user account or process from reading unlocked data in memory, and it does not protect content already sent to the desktop notification service, Pushbullet, OpenAI, a carrier, or another connected application. See the [privacy policy](../PRIVACY.md).

# Encrypted configuration and first-run setup

The existing application `pbdesk.sqlite3`, protected by mandatory SQLCipher encryption, is authoritative for non-secret settings. Core notification/web preferences and saved-summary presets already use SQLite; provider credential references, AI configuration and appearance/startup-page preferences now join them in the existing `settings` table. No new database or Alembic schema revision is needed.

The namespaced keys are `configuration:provider`, `configuration:provider_revision`, `configuration:setup`, `configuration:ai`, `configuration:ui` and `configuration:import_v1`. Provider references contain only backend/wallet/folder/entry, never the access token. AI credentials and encryption passwords remain in their existing secure-store entries.

## Established installations

During Store startup, the core imports `provider-credential.json`, `ai-settings.json` and `ui-preferences.json` from the configured state directory in one transaction. Existing SQL values take precedence. Only documented provider/AI fields are imported. During explicit history migration, the selected legacy files are verified and retained inside encrypted rollback material before the affected plaintext originals are removed. Once the import marker exists, subsequent JSON edits cannot override SQL. Invalid private credential or AI files stop the import explicitly, preserving the originals, instead of silently presenting a fresh setup.

An imported provider reference or existing provider account marks the installation established. Provider-wallet locking, permission denial and connection failures do not clear setup. Cached history remains available only after the separate database key is unlocked. Canonical reference errors are reported by Account settings without forcing first-run setup.

## First run

An unconfigured installation starts only the bootstrap core without reading KWallet. Before account setup, **Protect your local history** requires authorization of an existing KWallet for a dedicated random database key, then a saved recovery key or explicit backup decline. Existing plaintext profiles require explicit verified migration; missing keys or SQLCipher fail closed. See [encrypted history](encrypted-history.md). After protection/unlock and the recovery choice, the first authoritative configuration snapshot opens Settings → Account once per desktop launch. Cancelling leaves the page accessible without another prompt on every poll. Demo/fixture modes disable connection setup and never prompt.

The page offers an existing KWallet entry or a new token saved into an existing wallet. **Save and connect** is the account-setup action that reads/creates provider credentials or verifies the token with `GET https://api.pushbullet.com/v2/users/me`. Browsing the page and reading settings never access the wallet or provider.

New-token setup creates a missing folder only as part of the explicit save. It refuses existing entries and reserved encryption/AI entry names, and verifies the saved password by reading it back. Wallet approval has a longer bounded wait during interactive setup; ordinary background credential reads retain their short timeout. Failed token verification or wallet access leaves setup incomplete. Connection updates use an expected revision so stale forms cannot overwrite another client's saved selection.

The provider reference, setup marker and revision commit atomically, then the core reloads its provider through the existing guarded path. Cached history/account partitions and pending-action protections remain intact. Local CLI setup uses this same signed/authenticated core API and holds a temporary client lease during interactive setup.

## Preferences and API

- `GET /api/v1/configuration`: established/setup state, safe credential reference and UI preferences.
- `POST /api/v1/configuration/provider`: explicit verified connection setup.
- `PATCH /api/v1/configuration/preferences`: partial startup-page/color updates, atomically merged with existing fields.

Only the local desktop role can access these routes. Web/MCP roles and browser Origins are rejected. Validation and errors do not reflect token values or provider response bodies.

The desktop holds a session mirror, hydrates it from the core, and queues user changes made before the first response. A stale save acknowledgment cannot erase a newer local choice. The worker attempts to flush queued preferences before releasing its desktop lease on normal exit. The desktop never opens SQLite or writes replacement preference JSON files.

Launch paths/ports, private local service credentials, opaque history protection identities and restart handoffs remain bootstrap/runtime state. AI backoff/runtime records live in encrypted SQLite. They are not a second authority for user configuration.

Activation requires deliberately closing/restarting the older core and desktop, then completing explicit history migration/protection. This source change did not restart the current services or alter the live profile. The updated client rejects older cores rather than attaching to their plaintext history.

## Historical configuration verification

The following result describes the preceding configuration revision, before mandatory history encryption; it does not certify the encryption change.

The available regression suite passed: 467 tests, with one browser integration test deselected. Real coupled-process/restart tests and the MCP subprocess round trip could not run under this session's socket/IPC restrictions. The test runner bounded selector waits to 1 ms because the sandbox denies asyncio's socketpair wakeups; application event-loop code was unchanged. An earlier 10 ms test-runner bound exceeded a synthetic AI test's 80 ms retry window; the 1 ms run passed without changing AI retry behavior.

Coverage includes retained one-time import, SQLite authority after legacy-file edits, atomic preference merges, first-run versus locked established state, secret-free configuration, role/Origin restrictions, verification/write failures, stale connection revisions, native prompt cancellation/reentry, preference acknowledgments and exit flushing. Wallet and provider operations used synthetic values and fake transports. Two native setup modes were visually inspected at 850×540. A wheel built from a temporary source copy includes all new configuration/setup modules. No dependency was installed, live credential was changed, provider message was sent or live database was migrated during verification.

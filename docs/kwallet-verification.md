# Existing KWallet entry — verification on 2026-11-14

The regular editor profile now uses the existing password entry in wallet
`kdewallet`, folder `Pushbullet`, entry `api-key`. This is a folder/password
reference, not a new wallet. Its owner explicitly approved read-only Pushbullet
authentication and synchronization through the official API and event stream.

Only its location is saved in `.runtime/editor/provider-credential.json`, with
0600 permissions under the private state directory. The password was not moved,
duplicated into application files, or printed. The core reads it directly from
KWallet through bounded Jeepney D-Bus calls. Jeepney 0.9.0 was already installed
and pinned in both lockfiles; no dependency or OS package was installed.

## Local implementation checks

```bash
QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 \
  .venv/bin/python -m pytest -q -m 'not integration'
.venv/bin/python -m pip check
```

**47 passed; one installed-browser test deliberately deselected.** These include
12 credential tests covering exact folder/entry addressing, KWallet 6 service
selection, releasing only the application's handle, missing/denied entries,
sanitized errors, private non-secret references, refusal to fall back after an
explicit selection, legacy keyring setup, and exclusion of unknown fields from
diagnostic output. Application API, persistence, lifecycle, native, web, provider
and MCP regressions also passed. `pip check` found no broken requirements.

The updated wheel built without isolation or downloads. Its credential, CLI,
core API and KWallet module bytes match the current source, and metadata declares
the existing Jeepney pin. Artifact:
`.runtime/kwallet-verification/dist/pbdesk-0.1.0-py3-none-any.whl`.

SHA-256: `41e63592f2857147371ac7dde2354aa6734ac672615e713aa1827d2a00da9137`.

## Approved live checks

- The selected KWallet password was readable. The official read-only
  `GET https://api.pushbullet.com/v2/users/me` returned **HTTP 200**.
- The authenticated regular editor core was restarted to select the new source.
  Initial stored-history synchronization ran with notifications disabled, so
  historical records did not trigger a notification burst.
- The provider reached **connected**, after completing stored-push synchronization
  and opening the official Pushbullet WebSocket stream.
- The core retained one non-demo stored item. Restarting it with the normal
  notification adapter enabled changed the core instance but retained the same
  item count and event sequence, with no duplicate source identity.
- Sending and AI capabilities remain false. No outgoing message or AI request
  was made. Verification output contains aggregate counts and connection state,
  not message contents, source identifiers, account details, or secret values.

The regular editor core was intentionally left running for the existing desktop:
state `.runtime/editor`, core port 18765, web port 18766. The non-secret live
summary and package evidence are in `.runtime/kwallet-verification/`.

A fresh phone-triggered mirrored notification, its appearance in the existing
native window, and a real-account KDE popup remain to be verified. Use the Android
Pushbullet application's **Send a test notification** action, then check the
regular desktop profile. Encrypted notification decryption remains deferred.

The next implementation milestone after that live event check is core-owned
authorization and a persistent outbox before message composition or sending.

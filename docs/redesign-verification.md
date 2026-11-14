# Native slate and jade redesign verification

The implementation follows the accepted [requirements](../design/ui-review/accepted-requirements.md) and [selected visual direction](../design/ui-review/sol-direction.md). Generated proposal boards remain design references. The [implementation gallery](../design/ui-review/implemented/index.html) contains 24 actual production-widget and browser captures with fictional content, separate from the historical `current/` screenshots.

## Implemented scope

The native Qt Widgets shell has Inbox → Outbox → Devices navigation, linked Pushbullet/SMS device context, local reading panes, grouped SMS conversations, and phone Sent history separate from the pbdesk action ledger. Device management includes Linux registration, phone/browser setup guidance, rename, exact removal with history/credential preservation, shared or device-specific local KWallet references, masked entry creation, saved decryption testing, and explicit uncertain-registration recovery. Compose/edit/review retain exact payload, revision/fingerprint consent, unchecked transport acknowledgment, same-request creation reconciliation, and conservative uncertain outcomes. Settings, application/tray menus and Stop all preserve the established lifecycle distinctions. The optional responsive web companion exposes status only, with existing local vendor assets.

All device/provider/storage/credential operations go through authenticated HTTP in `CoreWorker`, outside the GUI thread. Cached SMS replies are tagged by device/request identity. Explicit account markers clear ephemeral caches on an account change, while old-account late replies are rejected. Local history remains readable after ordinary sync/key failures; device selection and reading position survive refresh. A removed destination remains explicit and cannot gain approval by silently using another device. Incoming source text is escaped before native formatting; no remote content or images are loaded.

## Final independent verification

The final [independent report](../.runtime/redesign-implementation/final-verification.json) passed on 14 November 2026. The application source remained unchanged throughout all checks. No dependencies were installed, no real provider or wallet mutation was performed, and all automated messaging/device operations used an injected fake provider.

| Gate | Final result |
| --- | --- |
| Complete pytest suite, including installed Firefox | **126 passed**, 8.40 seconds; zero skips, failures or errors |
| Non-browser suite with Node absent from PATH | **125 passed**, 1 browser test deselected, 5.13 seconds; Node did not resolve |
| Actual `CoreWorker` / `MainWindow` → authenticated loopback HTTP → production core API | **17 checks passed** with temporary state/application directories and a fake provider/wallet |
| Fresh native/browser captures | **24 passed**, every image hash verified and all 24 visually inspected |
| Wheel/source parity | **53 source/resource files** byte-identical to the frozen application source |
| Extracted wheel outside the repository with Qt imports blocked | Core/API/provider/storage/CLI imports and SQLite migrations passed |

The wire integration exercised cached SMS conversations, separate phone Sent history, device switching, sync and reading-position preservation through the real worker and authenticated HTTP API. It created one pending fake SMS, approved its exact payload through the real review dialog, and observed exactly one fake submission and its provider-acceptance state. Linux registration, rename, local key references/testing and exact removal also crossed the real HTTP boundary using fake provider/wallet operations. The added partial-availability check proves the unavailable conversation count and explanation cross authenticated HTTP into the GUI while cached conversation/Sent text and last-success time stay readable. There were **zero real provider operations and zero real wallet operations** in these verification gates. The [integration report](../.runtime/redesign-implementation/integration-verification.json) lists all 17 checks.

The complete suite includes recovery from stale upstream thread-index entries, retained authenticated dismissal controls, and stale device/account replies, mixed-account aggregate rejection, same-account key failures, explicit removed destinations, exact revision/fingerprint approval, creation reconciliation, uncertain registration, malformed retained timestamps, mode/search reconciliation and saved-action worker context. Firefox used the installed binaries, a dead external HTTP(S) proxy and isolated temporary directories; local assets, ordinary Refresh navigation and the 390px responsive layout passed. Restricted-sandbox asyncio/Wayland access required scoped escalation for the host checks.

Reproduction uses the existing environment:

```bash
.venv/bin/python -u .runtime/redesign-implementation/verify_final.py
```

The verifier bounds each subprocess to 60 seconds. The full pytest command was:

```bash
PBDESK_BROWSER_TEST=1 QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 timeout 60s .venv/bin/python -u -m pytest -q --junitxml=.runtime/redesign-implementation/final-tests.xml
```

The [package report](../.runtime/redesign-implementation/package-verification.json) confirms all five migrations, templates, CSS/JS, bundled Bootstrap/license and vendor manifest are packaged. The wheel was built without isolation or installation and tested from an extracted location outside the checkout.

| Artifact identity | SHA-256 |
| --- | --- |
| Frozen application, 53 files | `e16d318a10e6b36d4651b2032d6acdf32d2347aadf8c26e01efa6dd92dc8392c` |
| `pbdesk-0.1.0-py3-none-any.whl` | `7a391479e3558dd6024d2a80ab2efa522644dc3b7e2735d8060cafbe42021d37` |
| Screenshot manifest | `1abc34cbac0341ca2bdf4a06f04f3f23e3022bf073859956aad08bf9771c7b15` |

## Actual UI evidence

The independent fresh capture completed at **2026-11-14 07:06:45 UTC**:

```bash
QT_QPA_PLATFORM=wayland timeout 60s .venv/bin/python -u design/ui-review/capture_redesign.py
```

The helper uses actual production Qt Widgets on Wayland and installed Firefox with fictional data, a fixture worker and mock core transport. It performs no live account operation and reads no stored credential or real message. Native captures use `QWidget.grab()` of production windows/dialogs. The helper asserts the exact SMS-capable fictional phone, two populated conversations, one Sent row, incoming/outgoing text and an independent action ledger immediately before and after the SMS captures. The [capture manifest](../design/ui-review/redesign-capture-manifest.json) records individual image hashes; the [gallery](../design/ui-review/implemented/index.html) links all 24 full-resolution images.

| Capture | Evidence |
| --- | --- |
| `01-inbox.png`, `19-inbox-sms.png` | Source-device Pushbullet view; populated cached conversation with both SMS directions and independent refreshing/last-sync status |
| `03-outbox.png`, `20-outbox-sms.png` | Push/action view and phone Sent history distinct from pending actions |
| `02-devices.png` | Four-column master/detail, registration/capabilities, local encryption and always-visible history/actions at 1200×790 |
| `04`–`08`, `16` | Note/link/SMS composition, edit, unchecked exact review and uncertain outcome |
| `09`–`13` | Web settings on/off, Stop all consequence dialog, application/tray menus |
| `14`, `15` | Empty Inbox and retained reading during connection/key interruption |
| `17`, `18` | Responsive desktop and narrow status-only web page |
| `21`–`24` | Linux registration, phone/browser guide, local KWallet reference and exact removal; `24` explicitly guarantees local history is kept |

## Runtime and remaining acceptance boundaries

For the configured checkout, launch the existing **pbdesk: Desktop** profile/task in VS Code or Zed. It uses the editor state/credential references in `.runtime/editor`, core port **18765**, web port **18766**, and the application-directory database at the project root, `pbdesk.sqlite3`. A bare desktop launch with default environment settings selects a different state/port configuration and does not target that editor core.

## Operational activation and read-only account verification

The [initial activation record](../.runtime/redesign-implementation/activation-20261114T064756.409915Z/evidence.json) proves the configured database migrated from schema `0002` to `0005`: all four original items and four original ciphertext records retained their identities and original fields; the legacy database remained present and the credential-reference file was unchanged. Outbox and action audit counts were zero.

After the stale upstream thread-index fix, the [guarded restart record](../.runtime/redesign-implementation/restart-20261114T070945.793592Z/evidence.json) proves all **563 previously cached SMS**, **seven retained items** and **four ciphertext payloads** survived exactly, with no altered or missing rows. Action and audit counts stayed zero; the retained legacy database and credential-reference hash were unchanged.

The final [read-only live report](../.runtime/redesign-implementation/live-verification.json) **passed**. Signed core readiness and provider connectivity were observed twice consecutively. A launcher probe found the same running core, with both core/database ownership locks held. The account had five registered devices, including two SMS-capable phones; both phone-specific decryption states were `verified`.

| Live phone cache | Conversations | Incoming | Outgoing | Authenticated/decrypted SMS | Cache read | Unavailable upstream entries |
| --- | --- | --- | --- | --- | --- | --- |
| Phone 1 | 28 | 337 | 67 | 404 | 9.50 ms | 1 |
| Phone 2 | 28 | 580 | 36 | 616 | 12.94 ms | 2 |
| Total | 56 | 917 | 103 | 1,020 | — | 3 |

Both phones recorded a successful synchronization time, reconciled all server-available listed history, and accurately reported `partial` availability. Three stale entries in the upstream thread indexes returned HTTP 404. These entries no longer block the other conversations; any cached history for a missing conversation is preserved and the entry can be retried later. **A successful sync of the available entries is not proof of a complete all-time phone backup.** Authentication, HTTP 403 and network errors remain failures rather than being classified as missing conversations.

The global `key_ready` state means the shared key was loaded; the two phone-specific `verified` states separately establish authenticated decryption. The four old encrypted records were authenticated notification dismissal controls and remain retained as `ignored_control`, excluded from pending/error counts; their original ciphertext was not erased. The final encrypted-pending count was zero. See [protocol and control handling](sms-history-protocol.md).

The live report confirms account consistency, an empty outbox, web hosting off, and `0600` permissions for the application database, WAL, SHM and database lock. The operational checks performed read-only provider access and local ingestion/recovery. No packages were installed; no provider send, device registration/rename/removal, KWallet write, real SMS send or message-delivery claim was made. A fresh phone-triggered notification and any live send remain separate user-driven acceptance actions. Browser access remains a disabled-by-default, loopback-only status companion.

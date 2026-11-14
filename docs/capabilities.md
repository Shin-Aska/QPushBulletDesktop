# Capabilities and official API evidence

`/api/v1/capabilities` is authoritative. Implemented features are retained notifications,
FTS search, unified SMS conversation history, device management, per-device incoming E2E references, and approved note/link/SMS submissions. `sms_history` and `device_management` explicitly advertise the newer API surface; a connected older core is shown as requiring a shared-core restart before these routes are used.
`send=true` describes implementation; the separate `send_ready` and
`send_unavailable_reason` report whether the current provider can submit. `send_sms`
depends on the cached active SMS-capable device inventory. AI and web inbox remain
false. `encryption_state` and `encrypted_pending` report decryption independently
from connection state, so a missing password does not disable ordinary pushes.

| Capability | Implementation | Verification and official reference |
| --- | --- | --- |
| Stored pushes | GET `/v2/pushes`, server modified cursor and pagination | Fake tests; previous approved account sync/restart evidence in [KWallet verification](kwallet-verification.md). [Pushbullet API](https://docs.pushbullet.com/) |
| Mirrored notifications | Documented `mirror` ephemerals; device/package/notification ID/tag identity; persisted updates and deduplication | Sanitized fixtures; no promise of historical recovery. [Pushbullet stream](https://docs.pushbullet.com/) |
| E2E reading | Exact local password + `/v2/users/me` identity; PBKDF2-HMAC-SHA256 (30,000 iterations), AES-256-GCM; authentication before JSON | Official cross-language `hunter2` / `up0snaKOsn` key and `meow!` cipher vectors; fake locked/wrong-key/restart/reload tests. [Official encryption specification](https://docs.pushbullet.com/) |
| Device inventory | GET `/v2/devices`; bounded refresh, persisted safe fields, stale cache retained on failure | Fake optional-field/inactive/SMS capability tests; account activation results recorded separately. [Device API](https://docs.pushbullet.com/) |
| SMS history | Official client permanent objects: `{device}_threads` and `{device}_thread_{thread}`; paced reads, encrypted envelope recovery, stable identity merge | Protocol fixtures plus separately recorded read-only schema verification; see [SMS history protocol](sms-history-protocol.md). This is a client protocol, not a documented public REST history contract. |
| Device management | Register this Linux client, rename/remove exact registration, preserve history/credentials, resolve ambiguous registration after authoritative refresh | Fake provider/API tests; no live mutation performed |
| Per-device E2E | Same-folder shared/default or device-specific KWallet references; optional local entry creation and authenticated sample test | Fake locked/wrong-key/association tests; local test does not provision remote keys |
| Note/link sending | POST `/v2/pushes` with an explicitly selected active device; no broadcast | Fake HTTP and approval/outbox tests only. [Push creation](https://docs.pushbullet.com/) |
| SMS sending | POST `/v2/texts`, exact E.164 address and selected `has_sms` device; documented `data` fields and GUID | Fake HTTP tests only; no real send acceptance claimed. Deprecated SMS ephemerals are not used. [Text creation](https://docs.pushbullet.com/) |
| Approval/outbox | Core-owned revision/fingerprint/expiry-bound approval, durable claims and audit, conservative uncertain outcomes | Concurrency, edit invalidation, idempotency, cancellation, failure and restart tests; no real sends |
| Local search/events | Verified SQLite FTS5; transactional item/cursor snapshots; scoped bounded SSE replay/resync | Storage, API and blocked ASGI-send tests. [SQLite FTS5](https://sqlite.org/fts5.html), [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) |
| KDE notifications | One core-owned optional `notify-send` adapter, persistent at-most-once claim | Fake replay tests and prior host acceptance; DND can suppress popups |
| Status web page | Local Jinja and bundled Bootstrap; disabled by default; no message access | Existing smoke/assets/browser tests. [Jinja](https://jinja.palletsprojects.com/en/stable/api/), [Bootstrap](https://getbootstrap.com/docs/5.3/getting-started/contents/) |
| Local MCP | Official SDK stdio; read devices/items/actions and request pending actions; cannot approve/edit/cancel | Official Python client and role-isolation tests. [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/run/) |

The native sidebar is Notifications, SMS, Devices. Notifications search/source filters are independent of the SMS phone and conversation. SMS combines incoming/outgoing history in chat bubbles and saves inline drafts for exact review. The header/menu Message activity dialog retains every saved action regardless of the visible notification filter or SMS conversation. Provider acceptance, failed and uncertain requests remain distinct from phone-reported messages. Queued desktop action creation/reconciliation checks the current account before any POST and carries account metadata only between the GUI and worker.

## Encryption and sending boundaries

The configured KWallet source uses the **same wallet and folder** for `api-key`
and `e2e-password`. For the established setup these remain
`kdewallet` → `Pushbullet` → `api-key` / `e2e-password`. No entry is moved, renamed,
copied to a plaintext file, or requested from the browser/MCP client. The optional
verified keyring setup uses `pbdesk` / `pushbullet` and `pushbullet-e2e`. Password
Unicode and whitespace are preserved exactly. The password is never transmitted;
only the documented user identity is fetched to derive an in-memory key.

Incoming encrypted envelopes are retained before decryption, deduplicated by
cipher identity, and retried after credential reload/restart. Missing/wrong keys,
invalid frames and unsupported decrypted payloads do not silently erase received
ciphertext. Retry reads are batched; each envelope is limited to 1 MiB. Ciphertext
history follows retained local history and is not silently pruned by a queue cap.

**Outgoing note/link/text APIs are not claimed to be end-to-end encrypted.**
The implemented transport must be explicitly requested as `provider_plaintext`.
Desktop approval requires explicit consent and shows:
“Pushbullet can process this content. SMS carrier delivery is not end-to-end encrypted.”
The incoming E2E password is used only for local decryption; it is not sent to
Pushbullet or used to invent an undocumented `/texts` encryption format. A future
encrypted-only policy must reject unsupported transports rather than downgrade.

An upstream-accepted action means Pushbullet accepted the request, **not delivery**.
Timeouts, disconnects, cancellation during submission, 5xx responses and abandoned
submissions after restart become `uncertain` and are never automatically retried.
Definitive 4xx/429 outcomes fail with sanitized errors. UUID/GUID values provide
local request identity; they are not treated as a promise of provider idempotency.

Notification previews are separate from synchronized SMS conversations and are not used to construct an addressable conversation. No recipient is inferred from a notification title. SMS recipients
must be supplied explicitly in E.164 format.

## Deferred

AI, automatic sending/automation rules, broadcast, attachments, browser accounts/inbox, remote hosting/MCP, draft syncing,
retention settings and online backup/restore UI remain deferred. Web hosting is
still a status-only loopback gateway and cannot view retained message content.

The next acceptance step is a read-only configured-account device/encryption check.
A real send requires a separately reviewed exact destination, channel, device and
message approval; implementation tests never send real messages. After this
foundation, the next product milestone is authenticated web inbox/session storage,
CSRF protections and browser SSE before any remote access.

The Notifications/SMS revision passed independent final verification: **150 tests including Firefox**, **149 tests with Node absent** (one browser-marker test deselected), **23 authenticated worker/core HTTP checks against a fake provider**, and **13 independently inspected Qt captures on private Xvfb/xcb**. Its wheel preserves all 55 source/resource files byte-for-byte and extracted core imports/migrations pass without Qt. See the [final verification record](notifications-sms-verification.md) for exact wrappers, hashes and limits. The existing desktop, live core, root application database and KWallet defaults were left untouched; relaunch the existing **pbdesk: Desktop** profile to load the frontend, with no core restart or installation.

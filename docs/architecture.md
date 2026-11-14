# Core architecture

Only the standalone FastAPI core owns SQLite, Pushbullet connections, decryption,
the outbox, and notification claims. Qt, web and MCP clients use its authenticated
versioned loopback API; they never open SQLite or call Pushbullet. One Uvicorn
worker and a state-directory `flock` prevent multiple ingestion/dispatch owners.
The core imports no Qt, web templates or MCP SDK. Optional dependency groups remain
independent; `cryptography` is a pinned core dependency for authenticated decryption.
The original longer-term brief is retained in [specification.md](specification.md).

## Discovery and roles

The desktop starts or discovers the core from a worker thread. Discovery uses a
fresh 256-bit nonce and role-specific HMAC challenge; the response binds role,
nonce, instance UUID and readiness. An unrelated process occupying the core port
never receives the bearer credential during discovery. Only connection refusal
permits startup; mismatched proofs/ports and permission failures are explicit errors.
Transient `systemd-run --user` services are preferred; unavailable user service
managers fall back to detached foreground operation. `PBDESK_LIFECYCLE=foreground`
selects development mode. Login autostart is separate and is never enabled by launch.

Core and optional web ports default to 8765 and 8766 and remain independently
configurable through `PBDESK_CORE_PORT` / `PBDESK_WEB_PORT`; both bind only 127.0.0.1.
`PBDESK_STATE_DIR` selects an isolated state directory. Local service credentials
are random, distinct by role, revocable and atomic 0600 files under a 0700 directory.
Their presence is process separation within one Linux owner, not protection from
other malicious code already running as that owner. Credentials never appear in
URLs, argv, browser content or logs. Existing SSE streams detect revocation.

* Desktop may read and locally manage devices, credentials, web lifecycle and actions.
* MCP may read retained items/devices/actions and create **pending** action requests.
  It cannot approve, edit, cancel, reload credentials or refresh devices.
* The status web gateway may read health/capabilities only. It cannot read messages
  or create actions. There is no generic backend proxy or browser session yet.

All ordinary API calls require scoped bearer credentials after signed discovery.
Browser Origin headers are rejected; anonymous API docs/OpenAPI are disabled.

## SQLite and migration ownership

The application resolves `PBDESK_APPLICATION_DIR` independently of the Python interpreter; SQLite lives beside that application launcher. `PBDESK_STATE_DIR` retains service credentials and settings. An application database lock prevents two state directories from opening the same history store. Legacy state-directory databases migrate with a WAL-aware consistent backup and retained source; conflicting histories stop startup.

SQLite uses foreign keys, WAL, a 5000ms busy timeout and bounded transactions. FTS5
is probed before readiness. Migration `0001` contains frozen original definitions;
`0002` adds device cache, actions, audit and encrypted recovery tables; `0003` adds account/device/thread/message/direction-scoped SMS history, per-device local credential references, sync progress and encrypted history recovery; `0004` adds verified push source/destination associations and actual-decryption provenance; `0005` partitions retained items, actions, devices, encrypted recovery and relevant settings by account. Migrations retain existing history. Search indexes update through transactional
triggers. Search input becomes quoted literal FTS terms, never SQL. Item snapshots
and event cursors are read in one explicit SQLite read transaction.

Stored pushes use the provider `iden`. Mirror ephemerals use source device,
package, notification ID and tag in a separate identity namespace. Fingerprints
persist across restarts and exclude mirror receipt time. Changed mirrors update
one local item; repeats emit nothing. UTC timestamps distinguish provider creation
from mirror receipt. Dismissals/deletions do not erase retained local inbox history.

SSE carries item sequence/type/ID/instance UUID, not message text. Its replay window
is 256 durable events. Invalid/initial cursors resynchronize; readers that fall
outside replay resynchronize and disconnect. ASGI writes time out after five
seconds, avoiding unlimited queues. Clients retain ordinary refresh and poll device
and outbox status separately.

## Provider, devices and local decryption

Normal mode reads the selected provider token only through verified KWallet or
Secret Service. Existing KWallet configuration preserves the same wallet/folder
and reads `api-key` plus optional `e2e-password`; password whitespace and Unicode
are never normalized. Missing/locked backends never fall back to plaintext.
`--demo` and `--fixture` do not read real credentials or connect externally.
Tests can inject a fake provider factory at application construction.

One httpx client uses documented Pushbullet REST endpoints with ambient proxies
and redirects disabled. One bounded websocket reconciles stored pushes on reconnect
and push tickles, with exponential delay/jitter. Only finite nonboolean UTC
metadata can advance the remote modified cursor; local/server clocks validate
plausibility but never become that cursor. Invalid rows cannot poison progress,
and valid tombstones/dismissals still advance it. Provider URLs/exceptions are not
logged because the official websocket URL includes the API token.

Device startup/tickle/manual refresh caches only public fields: identity, nickname,
icon/type, manufacturer/model, active, push and SMS capability. Deprecated `pushable`
absence does not mean a documented active device cannot receive a push; explicit
false disables it. SMS requires explicit `has_sms=true`. Failed refresh retains
stale cache; dispatch requires a successful refresh before using it.

E2E derives an in-memory 32-byte key with PBKDF2-HMAC-SHA256, exact UTF-8 password,
`/v2/users/me` identity as salt, and 30,000 iterations. AES-GCM authenticates the
version/tag/12-byte-IV/ciphertext frame before JSON parsing. The password and key
are never stored in SQLite or transmitted. Official cross-language fixtures test
the primitive separately from JSON/message validation.

Encrypted envelopes persist before attempting decryption, deduplicated by cipher
identity. Each payload is limited to 1 MiB; unresolved records are not silently
pruned. Retry reads use batches of 100 and release the crypto lock between rows so
new ingestion can continue. Wrong/missing keys, malformed authenticated JSON and
unsupported decoded content remain recoverable with sanitized status. Successful
supported records are ingested through the normal identity/dedup path and their
cipher recovery row is removed. A single lock coordinates key replacement and
per-record decryption. `POST /api/v1/provider/reload` (or local
`pbdesk-admin reload-provider`) reloads the selected store and retries retained
ciphertext. Capability metadata exposes encryption state and pending count.

## Native device context and SMS history

Native Widgets use Notifications → SMS → Devices navigation. Notifications has an independent source-device filter and search for retained Pushbullet messages and mirrors. SMS owns its sending-phone selector, unified incoming/outgoing thread list, bubble timeline, and inline composer. The GUI owns only ephemeral display snapshots and unsaved per-account/phone/conversation drafts. Main forwards each original full or partial worker snapshot to the SMS component once; notification browsing never calls the worker's SMS device selection command.

Device switches render loaded SMS history immediately; the HTTP worker then reads cache-only `/api/v1/sms` and requests `/api/v1/sms/refresh` to queue provider work. Requests/results carry device, request and originating account identity; late results update only the matching cache without replacing another conversation or opening an unrelated review. Failed refreshes and missing keys preserve readable history and drafts. Stable conversation/message identities retain context and scroll. Account boundaries clear private client memory and close open review/edit dialogs. A queued account-bound action mutation checks fresh core capabilities before posting, even when the core process identity has not changed; originating account metadata is never added to the backend action payload.

Message activity is a separate native dialog available from the header and Application menu. It shows all Note, Link and SMS actions without reading filters and keeps its selection current through snapshots. Review/edit/cancel retain the same exact approval contract. Notifications composition creates Notes or Links; SMS composition starts inline and opens exact review only after a successful save in the same account/conversation. A saved action edited through the activity dialog never changes the current SMS phone, conversation or draft. No view change, poll, decrypt, or refresh enters the outgoing approval path.

The core reads official-client permanent objects for SMS thread lists and thread history, with paced/background reads, rate-limit handling, account/device partitioning, encrypted envelope recovery, and stable message identity/direction merge. A listed thread returning 404 increments explicit unavailable-count/partial metadata without blocking other available threads or deleting its cached history. Successful available-entry reconciliation records a last-success time; it is not a full all-time phone backup claim. Authentication, 403 and network failures remain errors. Incoming/outgoing rows form one SMS conversation timeline. Local pending/review/failed/uncertain actions are marked separately from phone-reported history and remain available in Message activity. Stored pushes retain independently verified source and target device identity; unassociated records are never assigned to the currently selected phone. See [protocol boundaries](sms-history-protocol.md).

Device mutations are desktop-only authenticated core operations. Registering creates a `pbdesk Linux` client with the actual returned identity, not a phone. An ambiguous registration blocks duplication until an authoritative device refresh and explicit outcome acknowledgment; an exact observed client may be adopted or the uncertain attempt explicitly abandoned. Removing a device preserves cached history and shared credentials and never retargets actions. Per-device KWallet references contain entry names, never values; optional masked entry creation writes only through core. `key_ready` is derived-key readiness; `verified` requires an authenticated associated ciphertext. A local reference/test does not configure another client remotely.

The worker checks `sms_history` / `device_management` capability flags per signed core instance. An older running core produces an actionable shared-core restart state while cached content stays readable.

## Approval-bound message actions

Every outgoing action first enters the persistent core outbox. The caller supplies
a UUID, channel, explicit active device, exact body/title/URL/recipient, expiry and
required `transport=provider_plaintext`. There is no broadcast. SMS additionally
requires a selected SMS-capable device and explicit E.164 address. Destinations
are never inferred from display names or notification previews.

Original request identity includes the requested duration, not a recalculated
current-time expiry. Repeating the same UUID and original content returns the
existing action, including after edits or submission. Reusing it for different
original content conflicts. Editable message fields use revision compare-and-swap;
every edit increments revision, recomputes the fingerprint and resets the action
to `pending_approval`, clearing approval. Expiry is immutable during edits.

Desktop-only approval must match revision and content fingerprint and explicitly
acknowledge provider plaintext. The fingerprint binds channel, destination, device,
exact content, transport and expiry. Approval never implies E2E transmission:
“Pushbullet can process this content. SMS carrier delivery is not end-to-end encrypted.”
MCP cannot approve itself by adding `confirmed=true` or another request field.

One dispatcher refreshes devices, checks expiry/capability and atomically claims
`ready → submitting` before any HTTP call. Claims verify the stored approval against
current content. Cancellation can win only while pending/ready; a claimed action
cannot be edited or cancelled. The dispatcher rechecks device/expiry immediately
before submission. Offline/failed device refresh leaves a ready action queued until
connectivity recovers or it expires; the approval remains bound to the same contents.

Note/link use documented POST `/v2/pushes` with `device_iden`. SMS uses POST
`/v2/texts` with documented `data.target_device_iden`, addresses, message and GUID.
The deprecated SMS ephemeral route and invented encrypted text formats are absent.
Outgoing payloads are ordinary provider-readable API data over TLS. The incoming
E2E password is used solely for local decryption.

Successful requests become `upstream_accepted`, not delivered. Definitive 4xx/429
rejections become `failed`, without retry. Network ambiguity, cancellation during
submission, 5xx and ambiguous responses become `uncertain`. Abandoned submitting
rows become uncertain on restart and are never automatically retried. Ready rows
survive restart only with the same approval/revision/fingerprint and unexpired
contents. No code assumes the provider deduplicates UUID/GUID values. Each create,
edit, approve, claim, result, cancel and expiry has a durable audit row with actor,
revision, fingerprint, state, timestamp and sanitized error code.

## Notifications, web lifecycle and recovery

Only the optional core notifier invokes existing `notify-send`, without shell or
Qt. A durable claim precedes submission, so replay/restart cannot duplicate the
same item popup. This is at most once: a crash/failure after claim may lose a popup.
Submission success does not prove a visible popup; Plasma DND can suppress it.
The launcher enables notifications; foreground mode opts in with `--notifications`.
Use quiet foreground mode for initial account sync to avoid historical popups.

Web hosting stays disabled by default. Its requested setting and serialized
listener transition are core-owned. A child `python -m pbdesk.web` must prove its
random launch identity before readiness. Disabling it closes its listener while
leaving the core alive. Optional web failure does not crash the core. Hiding or
exiting the desktop leaves core/web running; explicit stop-all stops both.

For a consistent backup, stop services and preserve the complete state directory plus the application-directory database and sidecars with their permissions, or use SQLite's supported backup procedure for the database.
Do not copy a live main database without WAL coordination. Restore with services
stopped and rotate local service credentials if trust has changed. OS credential
entries remain in KWallet/Secret Service. Automatic retention, attachments and
online backup/restore UI are deferred. New migrations are tested both on empty
state and preserved `0001` history/search data.

All automated send tests use fake providers; no paid AI, real accounts or sends are
required. Parent verification separately records actual host/account read-only
acceptance. Actual sends require a user-approved exact destination and content;
local implementation/testing does not constitute that approval.

The Notifications/SMS revision passed independent final verification: **150 tests including Firefox**, **149 tests with Node absent** (one browser-marker test deselected), **23 authenticated worker/core HTTP checks against a fake provider**, and **13 independently inspected Qt captures on private Xvfb/xcb**. Its wheel preserves all 55 source/resource files byte-for-byte and extracted core imports/migrations pass without Qt. See the [final verification record](notifications-sms-verification.md) for exact wrappers, hashes and limits. The existing desktop, live core, root application database and KWallet defaults were left untouched; relaunch the existing **pbdesk: Desktop** profile to load the frontend, with no core restart or installation.

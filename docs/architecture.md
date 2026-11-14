# Core architecture

Only the FastAPI core process owns SQLite, Pushbullet connections, decryption,
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
* The status web gateway may read health/capabilities and manage only its own client lease. It cannot read messages or create actions. There is no generic backend proxy or browser session yet.

Desktop-only AI/model/media routes use the same scoped authorization. `/api/v1/clients` accepts only desktop/web roles; instance-bound opaque leases are not available to MCP. All ordinary API calls require scoped bearer credentials after signed discovery.
Browser Origin headers are rejected; anonymous API docs/OpenAPI are disabled.

## SQLite and migration ownership

The application resolves `PBDESK_APPLICATION_DIR` independently of the Python interpreter; SQLite lives beside that application launcher. `PBDESK_STATE_DIR` retains service credentials and settings. An application database lock prevents two state directories from opening the same history store. Legacy state-directory databases migrate with a WAL-aware consistent backup and retained source; conflicting histories stop startup.

SQLite uses foreign keys, WAL, a 5000ms busy timeout and bounded transactions. FTS5
is probed before readiness. Migration `0001` contains frozen original definitions;
`0002` adds device cache, actions, audit and encrypted recovery tables; `0003` adds account/device/thread/message/direction-scoped SMS history, per-device local credential references, sync progress and encrypted history recovery; `0004` adds verified push source/destination associations and actual-decryption provenance; `0005` partitions retained items, actions, devices, encrypted recovery and relevant settings by account. `0006` adds retained notification/icon/file and SMS recipient/image metadata without replacing text, IDs, ciphertext or account/key references. `0007` adds four account-scoped saved-summary tables and three indices in the existing database, without rewriting existing tables or data. Migrations retain existing history. Search indexes update through transactional
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

Native Widgets use Summary → Notifications → SMS visual navigation, with reusable Appearance/Devices/AI/Local Web/Core tabs in Settings. Last-main-page and color preferences share atomic merged local UI storage. Visual rail order is separate from stable notifications/sms/summary logical page IDs, so saved startup context survives the reorder. Notifications has an independent source-device filter and search for retained Pushbullet messages and mirrors. SMS owns its sending-phone selector, unified incoming/outgoing thread list, bubble timeline, and inline composer. The GUI owns only ephemeral display snapshots and unsaved per-account/phone/conversation drafts. Main forwards each original full or partial worker snapshot to the SMS component once; notification browsing never calls the worker's SMS device selection command.

Device switches render loaded SMS history immediately; the HTTP worker then reads cache-only `/api/v1/sms` and requests `/api/v1/sms/refresh` to queue provider work. Requests/results carry device, request and originating account identity; late results update only the matching cache without replacing another conversation or opening an unrelated review. Failed refreshes and missing keys preserve readable history and drafts. Stable conversation/message identities retain context and scroll. Account boundaries clear private client memory and close open review/edit dialogs. A queued account-bound action mutation checks fresh core capabilities before posting, even when the core process identity has not changed; originating account metadata is never added to the backend action payload.

Message activity is a separate native dialog available from the header and Application menu. It shows all Note, Link and SMS actions without reading filters and keeps its selection current through snapshots. Review/edit/cancel retain the same exact approval contract. Notifications composition creates Notes or Links; SMS composition starts inline and opens exact review only after a successful save in the same account/conversation. A saved action edited through the activity dialog never changes the current SMS phone, conversation or draft. No view change, poll, decrypt, or refresh enters the outgoing approval path.

The core reads official-client permanent objects for SMS thread lists and thread history, with paced/background reads, rate-limit handling, account/device partitioning, encrypted envelope recovery, and stable message identity/direction merge. A listed thread returning 404 increments explicit unavailable-count/partial metadata without blocking other available threads or deleting its cached history. Successful available-entry reconciliation records a last-success time; it is not a full all-time phone backup claim. Authentication, 403 and network failures remain errors. Incoming/outgoing rows form one SMS conversation timeline. Local pending/review/failed/uncertain actions are marked separately from phone-reported history and remain available in Message activity. Stored pushes retain independently verified source and target device identity; unassociated records are never assigned to the currently selected phone. See [protocol boundaries](sms-history-protocol.md).

Device mutations are desktop-only authenticated core operations. Registering creates a `pbdesk Linux` client with the actual returned identity, not a phone. An ambiguous registration blocks duplication until an authoritative device refresh and explicit outcome acknowledgment; an exact observed client may be adopted or the uncertain attempt explicitly abandoned. Removing a device preserves cached history and shared credentials and never retargets actions. Per-device KWallet references contain entry names, never values; optional masked entry creation writes only through core. `key_ready` is derived-key readiness; `verified` requires an authenticated associated ciphertext. A local reference/test does not configure another client remotely.

The worker checks `sms_history` / `device_management` capability flags per signed core instance. An older running core produces an actionable shared-core restart state while cached content stays readable.

## Core settings and quiescent restart

The authenticated desktop-only `/api/v1/core/settings` read reports signed instance identity, uptime, launch-controlled coupled/standalone mode, active desktop/web-process lease counts, addresses, application/state/database paths, lease timings and notifier state. The Core tab presents launch-controlled values read-only. `PATCH /api/v1/core/settings` carries `expected_instance_id` and an explicit notification boolean; the core persists `core_notifications_enabled` in its store and configures the notifier live. Requested preference, available adapter and effective delivery state remain distinct. On startup the persisted override takes precedence over the original notification launch flag; absence of an override preserves that launch flag. Submitted notification delivery does not guarantee a visible popup.

`POST /api/v1/core/restart` carries the expected current instance and a caller request ID. Under the dispatch/approval interlock it rejects ready/submitting actions, active provider mutations, provider reload or dispatch work. A restart gate tracks asynchronous mutation ownership until underlying work actually completes, including cancelled requests whose thread/task could still commit. Cancellation therefore cannot make a non-quiescent backend appear restartable. A repeated matching accepted request can identify its original acknowledgement, but clients never automatically replay mutation/settings/restart HTTP after a timeout or recovery.

The core publishes a private 0600 validated handoff bound to its old instance and current Stop All generation, with a maximum 60-second lifetime and its exact demo/fixture/standalone/notification launch options and lease timings. A detached helper performs a readiness handshake, holds no client lease and waits for signed old-core exit before serialized replacement launch. Invalid/expired handoffs fail explicitly rather than falling back to ordinary desktop launch defaults. Persisted notification settings remain in the existing store.

Deliberate restart leaves the desktop process, views and unsent drafts intact. It does not stop an enabled web server: the replacement core verifies/adopts the same server PID/instance rather than opening a duplicate listener. Desktop confirmation requires a different verified core instance, not merely a successful POST. The Stop All generation is unchanged by Restart core. Stop All separately advances the generation and cancels/invalidates pending helpers and old clients, preserving the no-revival barrier. Embedded custom launch profiles without a supported restart profile refuse self-restart and require an explicit external process restart.

## Saved summaries, provenance and reply assistance

`Store.summaries` uses the existing core-owned SQLite engine. Additive `0007` creates `summary_presets`, `summary_state`, `summary_generations` and `summary_entries`; it does not create another database. Explicit account IDs scope every repository/API read and write. Preset edits, the shared active selection and retention policy use independent optimistic revisions. Archival retains history and selects a fallback when the active preset is archived.

One active preset per account is shared across clients. Defaults All activity and Notifications only use 24 hours. Presets capture notification/SMS sources, device filters, exact `(device_id, conversation_id)` pairs, one of 1/6/24/72/168 hours and an optional model override. Selected-conversation mode requires a nonempty SMS tuple selection; no empty broadening occurs. Cached options are bounded metadata reads, not provider refreshes. Preset/history/options browsing is cloud-free. Creating/updating inactive presets never activates them. Saving changes to the active preset deliberately applies its scope at the next eligible automatic update, with a UI cloud-use disclosure; there is no separate active-snapshot table.

Generation identity is account + canonical semantic config hash + resolved model + prompt version + input fingerprint. The semantic hash excludes display name and separately resolved model override. Immutable generated text and actual nullable metrics are shared when that identity matches. Per-preset-revision dated history entries retain full captured config, origin, saved date and original generation date, independently of mutable pin/reuse metadata. Renames, later edits, activation changes and reuse never rewrite captured scope or original dates. Unchanged polls append no history. Already-paid in-flight work saves its captured old preset/model provenance before later active work proceeds.

The active preset alone drives automatic generation. The core selects matching cached text, at most 100 records/24,000 characters, coalesces input identities, serializes cloud work and enforces a global minimum 60-second automatic paid cadence. UI status polling remains five seconds with a two-second debounce; empty matching text makes no cloud request. Last-good output stays visible while queued/updating/error. SQL is authoritative for outputs/cache/history. Valid private legacy JSON is imported account-scoped and idempotently, with unknown context/usage fields null and the source file untouched. Private `ai-runtime-state.json` persists only attempt/backoff/latch metadata, not summary entries.

History reads support literal search, captured preset identity including archives, pinned/date filters and bounded pagination. UI calendar-day filters are converted to timezone-aware boundaries. Details report actual nullable token usage, cached-input tokens, monotonic measured latency, response/configured model, prompt version, coverage/truncation and reuse dates/count; unknown is distinct from zero. Pricing is not inferred. Retention defaults to 30 days, allows 7/30/90/365, and previews an unpinned affected count before explicit revision-bound confirmation. Pins survive pruning; generated rows are removed only after their final entry reference is gone.

Latest/history Markdown is rendered natively with embedded HTML, images and resource loading blocked by the custom document/browser guard. HTTP(S) links require validated explicit activation. The effective palette, unchanged-text selection/scroll and existing Qt synchronous stylesheet wrapper-ownership guard remain intact. No new rendering dependency is installed.

AI configuration still uses the separate `kdewallet → Pushbullet → ai-openai-api-key`, preserving provider/encryption entries. The editable fixed-host model catalog preserves custom IDs. Generation uses fixed OpenAI Responses, `store: false`, no tools/conversation reuse, bounded input/output and cancellation/account checks. Default `gpt-6-luna` uses reasoning `none`, verbosity `low`, and automatic digest output at most 1,000 tokens. Store suppression does not establish training policy or zero data retention.

The compact SMS assistant opens with local context preparation only, collapsed context and one large editable draft. An explicit Draft reply action chains fresh prepare and Generate. Cancellation/late-result/editor-change guards protect user text. Use draft inserts the current edited text only if the account/device/thread/composer/pending token remains valid, asks before replacing a nonempty composer, then closes and focuses the composer. Read-only/group/history-only drafting can remain copyable without enabling insertion. No assistant action creates/approves/sends a message; normal exact Review & send remains required. Core shutdown/restart cancels and drains AI jobs/owned SQL threads before closing Store.

SMS AI Draft exposes the latest 20 cached conversation messages in a local table with checkbox, time, sender and wrapped plain-text body. Preview refresh preserves checked IDs that remain available; newly arrived messages start unchecked. Zero checked messages disables drafting. Draft preparation submits only checked IDs, and the core validates that each remains in the exact conversation's current latest-20 window. It never substitutes unchecked or omitted message bodies into generation input. The editable tone prompt appears for Draft again, defaults to Natural, and accepts Friendly, Brief, Professional, Warm or custom text up to 200 characters. Canceling it makes no request; an accepted choice is remembered. A prior draft is retained on cancellation or failure, and edits made while generation runs are preserved. Use draft inserts editable text into the SMS composer, closes the assistant and focuses the composer; the regular Review & send path remains required. See [SMS tone/context contract](../.runtime/sms-tone-context/backend-contract.md) and [verification record](sms-tone-context-verification.md).


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

Web hosting stays disabled by default. Its requested setting and serialized listener transition are core-owned. A child `python -m pbdesk.web` proves a random launch identity bound to the Stop All generation before readiness. After a core crash, a surviving signed web process can be adopted without opening a duplicate listener. An unrelated listener is never adopted or killed.

Normal core launches are client-coupled. Each desktop/web **server process** registers an opaque lease bound to the core instance, role and launch generation, then renews from an independent heartbeat thread every five seconds. A lease expires after 45 seconds; after the final release/expiry the core allows a three-second grace before shutdown, with a 20-second startup grace before the first registration. Tray hiding retains the desktop lease; full Exit releases only that desktop. Another desktop or web server keeps the core alive. Browser tabs hold no lease, so closing a browser does nothing. Disabling/stopping the web server releases its lease; the core remains only while another client or explicit standalone mode requires it.

A client can recover a crashed core with signed discovery, serialized startup and bounded retry; failed message mutations are never replayed. Stop All stops the web/core and atomically advances a persistent generation under the startup lock. Old clients cannot relaunch that stopped generation, while a fresh launch can start normally. Explicit `pbdesk.cli --standalone` opts out for debugging/installed-service use; ordinary transient `ensure_core` launches remain coupled. `python -m pbdesk.web` manages a core by default, while `--standalone` is a debug status host that can show an absent core without starting one. MCP is a read/action-request adapter rather than a lifecycle owner: it holds no lease and cannot keep a coupled core alive alone. Headless MCP automation requires an explicitly started standalone core. No launch enables login autostart.

For a consistent backup, stop services and preserve the complete state directory plus the application-directory database and sidecars with their permissions, or use SQLite's supported backup procedure for the database.
Do not copy a live main database without WAL coordination. Restore with services
stopped and rotate local service credentials if trust has changed. OS credential
entries remain in KWallet/Secret Service. Automatic retention, outgoing attachments and online backup/restore UI are deferred; supported incoming image previews are implemented. New migrations are tested both on empty
state and preserved `0001` history/search data.

All automated send tests use fake providers; no paid AI, real accounts or sends are
required. Parent verification separately records actual host/account read-only
acceptance. Actual sends require a user-approved exact destination and content;
local implementation/testing does not constitute that approval.

Current saved-summary/SMS verification status is recorded in [saved-summaries-sms-verification.md](saved-summaries-sms-verification.md). Independent final gates passed, followed by guarded `0006`→`0007` activation of the configured editor core with database/web/generation/profile/preference preservation checks. The desktop still needs relaunch. Previous [Core Settings](core-settings-summary-verification.md), [automatic Summary/lifecycle](auto-summary-lifecycle-verification.md), [media/AI/Settings](media-ai-settings-verification.md) and [Notifications/SMS](notifications-sms-verification.md) records remain historical. Keep the existing database/profile; activation must preserve its prior history and key references.

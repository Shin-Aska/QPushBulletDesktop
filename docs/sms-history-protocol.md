# SMS history and local synchronization

pbdesk reads the SMS conversation snapshots used by Pushbullet's current web client. These are history reads. Reading, device selection, decryption and refresh never call the outgoing `/texts` or `/pushes` endpoints or submit an action.

## Verified protocol

Primary source inspected on 2026-11-14: [Pushbullet's official client JavaScript](https://www.pushbullet.com/js/pushbullet.js), `SmsApiSuite.fetch_device` and `SmsApiSuite.fetch_thread`. SHA-256 of the decoded UTF-8 response: `c65bd077d5d2c349935654cc426270c360d592c2593465abc6b1f334bd23bd1f`.

* `GET https://api.pushbullet.com/v2/permanents/{device_id}_threads` returns an object with `threads`, a list of conversations.
* `GET https://api.pushbullet.com/v2/permanents/{device_id}_thread_{thread_id}` returns an object with `thread`, a list of messages.
* Either object can instead be `{ "encrypted": true, "ciphertext": "..." }`. Authenticate and decrypt before parsing its JSON. The permanent-object key binds the account/device context; explicit decrypted account/device fields must agree.
* Conversation fields include `id`, `recipients`, and `latest`. Recipients have `name` and `address`; `number` is supported as an older-client fallback. The official client also supplies inline base64 `thumbnail` images and optional remote `image_url` contact images; messages can contain MMS `image_urls`.
* Messages have `id` (or `guid`), `direction` (`incoming` or `outgoing`), `body`, and Unix `timestamp`. Attachments may have `image_urls`. pbdesk normalizes timestamps to float UTC epoch **seconds** (Android millisecond values are converted). Messages with unsupported identity, direction, timestamp or body are not assigned a guessed identity.
* A plain or authenticated decrypted stream control push with `type: sms_changed` queues a history refresh. It does not become a fake Inbox message. An explicit source device is used only when it identifies a cached SMS-capable device; otherwise the known SMS devices are refreshed.

The current official methods have no cursor, older-page call, or pagination parameter. pbdesk retains every valid message returned by each permanent snapshot and merges new snapshots without deleting older cached rows. It does not invent an all-time phone-backup guarantee or fabricate remote pagination. `sync.has_more` is false for this snapshot protocol. Provider push/device feeds independently retain their real pagination support.

Read-only live verification is recorded in `.runtime/redesign-implementation/provider-schema.json`: two SMS-capable devices returned HTTP 200 encrypted conversation indexes (29 and 30 conversations); one sampled thread per device returned HTTP 200 encrypted histories (40 messages each), with actual incoming/outgoing direction strings and integer second timestamps. The evidence records schema/counts only, with no tokens, device identities, bodies, recipient values or names. No send, registration, rename, removal or wallet write was performed. Full application synchronization remains separately verifiable at runtime.

The historical interoperability source [pidgin-pushbullet](https://github.com/EionRobb/pidgin-pushbullet/blob/master/libpushbullet.c) also reads the same permanent-key convention via the older v3 get-permanent request. pbdesk uses the verified current official client's v2 GET protocol.

## Local-first API and scheduling

`GET /api/v1/sms?device_id=ID&direction=incoming|outgoing` reads SQLite only. Omit direction for both directions. It returns `{messages, sync}`. Each message contains local integer `id`, `account_id`, `device_id`, `conversation_id`, stable string `message_id`, `direction`, `body`, float `timestamp`, `addresses`, optional `name`, boolean `decrypted`, retained `image_urls`, and bounded `recipients` metadata (address/name/thumbnail/image_url when supplied). `sync` contains `state`, `last_success_at` (float epoch seconds or null), sanitized `error`, and `has_more`.

`POST /api/v1/sms/refresh` with `{device_id}` queues work and returns cached data promptly. The core owns a single background queue, coalesces repeated device requests, spaces permanent reads by at least 350 ms, polls every five minutes, and refreshes on reconnect and SMS changes. `Retry-After` for 429 responses is honored with finite bounds of 1–3600 seconds. Conversation fingerprints skip unchanged threads only when the provider gives a stable latest-message identity and timestamp. A completed merge and its fingerprint are atomic. Each message identity includes account, device, conversation, message ID and direction.

Failures retain all readable cache, unresolved ciphertext, and last successful sync. Partial progress is durable; only a fully completed device pass advances last success. Interrupted refreshing states recover to idle on restart. Source/destination changes in a view never change saved actions. Account changes use a distinct push-feed cursor and SMS partition. The current account's SMS cache is returned; another account's rows remain retained.

## Retained native media

Migration `0006` adds optional notification `application_name`, `icon`, `file_name`, `file_type` and `file_url` fields, plus SMS `image_urls` and `recipients`. It is additive: existing text, IDs, ciphertext, settings and account/key references remain intact; old rows start with empty media metadata. SMS conversation fingerprints include a normalizer version so unchanged pre-media threads receive an ordinary paced reread. This does not bypass provider scheduling or reconstruct fields absent upstream. Previously discarded mirrored-notification icon metadata may require a new notification event to become available.

The desktop draws supplied icons and inline contact thumbnails natively. Remote contact/MMS/file-image previews require an explicit Load click. `GET /api/v1/media/image` accepts the current `account_id` and a retained item/message/contact identity plus image index; it does not accept a caller-supplied URL. Only the desktop role can use it. The core checks account ownership before and after fetching and resolves the stored metadata itself.

Supported origins are exact HTTPS hosts: `dl.pushbulletusercontent.com`, `dl3.pushbulletusercontent.com` (also observed in retained provider contact metadata), `static.pushbullet.com`, `pushbullet.imgix.net`, and `lh1.googleusercontent.com` through `lh6.googleusercontent.com`. Redirects are rejected. Fetches use a separate environment-independent client without provider authorization, access-token or cookie headers, with finite timeouts and a 4 MiB response limit. JPEG, PNG, WebP, GIF and BMP raster content is subject to native decode/dimension limits. Unsupported, oversized, expired or malformed images remain unavailable with local retry feedback; plain text and the composer stay usable. Decoded preview caching is bounded in desktop memory, cleared on account change, and never written as an image disk cache. See the [official Pushbullet API](https://docs.pushbullet.com/) and pinned public-client source above for upstream shapes.

This backend API/schema revision requires restarting both core and desktop, rather than only relaunching the frontend. Use Application → Stop all services…, close the old desktop, then start the existing pbdesk: Desktop profile. Other clients disconnect; preserve the existing profile, database and KWallet folder. The [media/AI/Settings verification record](media-ai-settings-verification.md) covers isolated migration retention and authenticated HTTP checks; no live restart was performed for that verification.

## Local key references

SQLite stores references (`mode`, `entry`) scoped by account/device, not passwords or derived keys. Shared mode means the existing `kdewallet → Pushbullet → e2e-password`. Device mode selects an existing entry or explicitly creates a new entry in that same wallet/folder. `api-key`, the shared `e2e-password`, and any existing entry cannot be overwritten by creation. Removing a registration never deletes history or wallet entries.

`POST /api/v1/devices/{id}/encryption` accepts `{mode, entry?, password?}`. Passwords are never reflected in results or validation errors. `POST .../encryption/test` tests the saved reference, returns `encryption`, `tested`, `verified`, and `detail`, and queues relevant history work. `key_ready` means a key was derived; only successful authenticated decryption with verified association produces `verified`. This is local configuration and does not change a phone's password remotely. Multiple device keys may be tried for an envelope whose source is hidden, but the decoded source must match the key's associated device. Retained ciphertext can be replayed locally when keys become available.

## Device management

`POST /api/v1/devices {nickname}` registers only a pbdesk Linux client and saves the actual returned identity. `PATCH /api/v1/devices/{id} {nickname}` renames that exact registration. `DELETE /api/v1/devices/{id}` removes that exact remote registration and deactivates its cached capabilities, retaining history/actions/credentials. Mutations serialize with dispatch so saved actions cannot be silently rerouted. An active registration does not prove that a device is online; `reachable` is null without an observation.

Ambiguous registration outcomes persist across restarts and block blind duplicate registration. Refresh authoritative devices, then explicitly resolve with `POST /api/v1/devices/registration/resolve {device_id, acknowledge_unknown_outcome:true}`. An exact observed pbdesk Linux identity adopts that registration. A null identity explicitly abandons the uncertain attempt after review, enabling a later deliberate Add. No automatic registration retry occurs.

These mutations and history APIs are desktop-only. The web companion remains status-only; MCP retains its existing read/request-action restrictions and cannot invoke device/key mutations or refresh.

## SQLite placement and migration

`PBDESK_APPLICATION_DIR` explicitly names the application directory. Console launchers use their own directory, source/module launches use the project launcher directory, and frozen applications use the packaged executable directory. The path never derives from `/usr/bin/python`. Explicit `Settings(tmp_path)` defaults its application directory to that same temporary state path for isolation. Launchers and web subprocesses propagate the application directory. IDE demo configurations name their separate demo directory.

The core owns `APPLICATION_DIR/pbdesk.sqlite3`; discovery files, service credentials, provider-reference JSON and state locks stay in `PBDESK_STATE_DIR`. A database-directory lock prevents distinct state directories from opening the same application database. The file is created 0600 before SQLite opens it; WAL sidecars inherit private database permissions.

After the old core is stopped, a legacy state database is migrated by SQLite's WAL-aware backup API, integrity checked, checkpointed, closed, and atomically renamed into the application directory. The original is retained. A provenance marker records the retained source's logical digest. An existing destination with an unrelated or subsequently changed legacy history is rejected explicitly instead of silently selecting a database or discarding either history. Alembic migrations retain prior pushes, FTS, actions and uncertain outcomes.

## Account transitions and response consistency

Migration `0005` partitions retained pushes, actions, inventory and encrypted recovery by account as well as the SMS/key tables. The first authenticated account atomically claims pre-account legacy rows. Returning from account B to A restores A's retained rows and uncertain registration state; switching accounts does not expose or dispatch A's commands under B. Caller request UUIDs and retained provider source identities use an internal account namespace while public identifiers and outgoing SMS GUIDs retain their existing representation.

Items, actions, devices, and SMS list responses include `account_id`, including empty results. Items/actions/devices assemble the marker and their rows from the same SQLite read transaction. Devices include key references, history counts, and scoped registration/sync metadata from that snapshot. Clients must discard mixed-account aggregate responses and late history replies. Provider inventory writes carry the expected authenticated account and reject a stale response after an account transition. Device/key mutations serialize with provider replacement; a key-reference write verifies the captured account in its transaction.

Capabilities also contain `push_sync: {state, last_success_at, error}`. `last_success_at` is a local float UTC epoch second clock observation after a complete successful push synchronization, separately from the provider's modified cursor. A failed refresh or interrupted process preserves the previous last-success value.

## Guarded editor-core activation

The reviewed helper `.runtime/redesign-implementation/activate_core.py` defaults to preflight:

```sh
.venv/bin/python .runtime/redesign-implementation/activate_core.py
```

It verifies signed identity of the existing editor core on port 18765, checks web status on the authenticated API (web port 18766), hashes the existing credential-reference file, and takes a private WAL-aware SQLite snapshot. It records counts and SHA-256 digests of every SQL table without printing their contents. Any `ready` or `submitting` action rejects activation. It refuses an existing application-directory destination rather than overwrite one.

After integration gates, the parent operator explicitly runs:

```sh
.venv/bin/python .runtime/redesign-implementation/activate_core.py --activate
```

This requests authenticated local shutdown, waits for both ports and the state lock to release, makes a final stopped snapshot, and checks action states again. It then launches the existing virtualenv's foreground core with the project root as `PBDESK_APPLICATION_DIR`, the original editor state directory, and ports 18765/18766. No historical notifications are requested. Only the new core performs migration; the helper never writes either live database. No wallet or provider mutation is called.

The helper verifies signed readiness, snapshots the new database, records original-row retention and credential-reference equality, and leaves all original databases and private backups intact. Readiness is not a claim that remote synchronization has completed; inspect actual API synchronization metadata afterward. Successful decryption may consume retained cipher-queue rows, while the original ciphertext remains in the untouched source/backup. If any step fails, inspect the recorded private evidence and core log; do not repeat registration, sends, or migration blindly.

## Stale conversation indexes and authenticated controls

Live verification found stale conversation-index entries whose individual permanent object returns HTTP 404, while later entries remain available. A missing thread no longer aborts the rest of a device's pass. The core preserves that conversation's cached messages, records missing-key metadata scoped by account/device, continues later threads, and deliberately leaves its successful fingerprint incomplete so the next pass retries it. Network, authentication, rate-limit, other HTTP errors and malformed content still fail the pass and retain the previous last-success timestamp.

A completed reconciliation with missing indexed permanents returns `sync.state: "partial"`, a positive `sync.unavailable_count`, a concise detail in `sync.error`, and the completion time in `last_success_at`. That timestamp means the available index was reconciled; it does not claim that every indexed permanent exists. A later successful retrieval clears the corresponding unavailable count. The existing desktop sync-detail line displays this limitation while keeping cached conversations readable.

A sanitized read-only check of the remaining index entries found one missing permanent on the first phone and two on the second. The later available entries contained 228 and 229 additional valid messages respectively, with no normalization failures. This diagnosis used official GET reads, did not modify either database or KWallet, and printed no identities or content. Final imported counts are verified separately after the guarded restart.

The four pre-existing unresolved encrypted records were authenticated notification `dismissal` controls, rather than wrong-password failures or Inbox messages. Known dismissal controls validate notification identity, package, decoded device association and any explicit source-user association. Their original ciphertext rows are retained as `ignored_control`, excluded from pending/error counts and retry work. They never create fake Inbox records or erase retained history. Invalid controls and actual authentication/format failures remain recoverable pending errors.

The already-migrated application database uses a separate guarded restart helper:

```sh
.venv/bin/python .runtime/redesign-implementation/restart_core.py
.venv/bin/python .runtime/redesign-implementation/restart_core.py --restart
```

The first command performs preflight only. The second explicitly restarts a signed running core after checking no ready/submitting actions, taking private WAL-aware snapshots of the root database, and waiting for both ports and database/state ownership locks. It starts with the same state/application directories and checks retained push/action/SMS identities, unchanged original ciphertext payloads, unchanged retained legacy database, and unchanged credential reference. It does not rerun the first-migration helper, overwrite the legacy source, call provider mutation endpoints, or write KWallet.

The restart preservation gate compares original stored values as well as identities. Content changes or missing rows require review even if their IDs remain. Documented metadata-only exceptions are recomputed item fingerprints/local-decryption flags, SMS local-decryption flags, authenticated control classification, and ordinary pending-action expiry metadata. Ciphertext payloads and action-audit originals must remain exact. Previously missing conversations are retried even if the provider index rolls back to an older fingerprint that matches the cached thread.

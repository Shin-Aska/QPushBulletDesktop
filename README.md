# pbdesk

[License](LICENSE) · [Privacy policy](PRIVACY.md) · [Changelog](CHANGELOG.md) · [First-release plan](docs/release-plan.md) · [Encrypted history](docs/encrypted-history.md) · [Private AppImage builds](docs/appimage.md)

A Linux-first Pushbullet desktop for Kubuntu 26.04, KDE Plasma 6 on Wayland, and standard CPython 3.14. One private Python core owns the SQLCipher-encrypted SQLite history, provider ingestion, notification delivery, and the optional web-host process. The native interface uses PyQt6 Widgets; no embedded browser, Node.js, CDN, or JavaScript build system is required.

pbdesk is licensed under the [GNU General Public License, version 3 only](LICENSE) (`GPL-3.0-only`). Third-party components retain their own licenses and distribution requirements. This is an independent client; the project license does not grant rights to Pushbullet's service, trademarks, or proprietary software.

The native application reads stored pushes, mirrored notifications, and synchronized phone SMS conversations; manages Pushbullet device registrations and local decryption references; and sends explicitly addressed notes, links, and SMS through a persistent approval pipeline. Encrypted incoming content is decrypted in the core using local KWallet credentials. Missing or incorrect incoming-content keys preserve readable cached history and encrypted incoming recovery records once local history is unlocked. The separate database key is mandatory: an unavailable database key blocks history access. Automatic recent-activity summaries and reviewed SMS reply drafts use OpenAI after local key setup; automatic sending rules and browser inbox access remain deferred. See [feature parity](docs/feature-parity.md) and the retained [specification](docs/specification.md).

The sidebar is ordered **Summary → Notifications → SMS**, with **Settings** at the bottom. Settings is one reusable nonmodal dialog with **Appearance**, **Account**, **Devices**, **AI**, **Local Web**, and **Core** tabs. Notifications retains Pushbullet messages and mirrored notifications, with source filtering and search. SMS has its own phone selector, a conversation list, incoming/outgoing chat bubbles and a composer beneath the timeline. Browsing notifications never changes the SMS sending phone. New message starts an inline SMS draft; Ctrl+N follows the Notifications/SMS page and Ctrl+F focuses its search. In Settings → Devices, F5 refreshes devices and Ctrl+F focuses the device filter; Ctrl+N does not compose behind Settings.

**Message activity** in the header or Application menu lists every saved Note, Link and SMS action, including failed and uncertain outcomes. Notifications Compose creates Notes or Links. SMS **Review & send** saves a pending action and opens the existing exact review when its account and conversation are still current. Saving sends nothing: approval still requires the displayed destination, revision, fingerprint, expiry and unchecked plaintext consent. Editing a saved SMS action leaves the current conversation draft untouched. Unsaved SMS drafts are local to the running window and its account/phone/conversation; restart persistence is deferred.

SMS AI Draft offers a local checked context of up to the 20 most recent cached messages and a tone choice on Draft again. See [SMS tone and context verification](docs/sms-tone-context-verification.md) for its contract and current verification status.

Independent [saved summaries and SMS reply verification](docs/saved-summaries-sms-verification.md) passed **416 repository tests**, **304 focused tests**, **27 lifecycle checks** and **2 genuine production Qt/Core HTTP integration checks**, with zero final failures, errors or skips. The [current fictional gallery](design/ui-review/saved-summaries-sms/index.html) contains **89 inspected captures**: 81 current native component views, seven genuine connected HTTP views and one historical before image. These overlapping test scopes are not an aggregate total. Earlier [Core Settings](docs/core-settings-summary-verification.md), [automatic Summary/lifecycle](docs/auto-summary-lifecycle-verification.md), [media/AI/Settings](docs/media-ai-settings-verification.md) and [consistent UI](docs/consistent-ui-verification.md) results remain historical; their counts do not certify this revision.

**Mandatory local-history encryption is implemented in this source revision; the currently running older core and existing profile have not been migrated by this work.** After deliberately closing/restarting the older core and desktop, **Protect your local history** appears before account connection. Authorize your existing KWallet, then save a recovery key or explicitly choose **Continue without backup**. Existing plaintext history requires the explicit **Encrypt existing history** migration. The new runtime has no plaintext fallback. See [encrypted history, migration and recovery](docs/encrypted-history.md) before activation.

The earlier [configuration and first-run setup record](docs/configuration.md) describes the preceding settings import. In this revision, non-secret settings and AI runtime/cache state live inside encrypted SQLite; selected legacy configuration/cache files are verified, archived inside encrypted migration material, and removed only after successful migration. Historical verification counts above and below do not certify the encryption change. No live wallet access, profile migration, provider send, OpenAI request, or service restart was performed for this change.

The prior [Notifications/SMS verification record](docs/notifications-sms-verification.md) retains its historical **150 passing tests including Firefox**, **149 passing tests with Node absent**, **23 worker/core HTTP checks against a fake provider**, and [13 native Qt captures](design/ui-review/notifications-sms/index.html). Those results describe the earlier revision.

## Run the native desktop

The development environment is `.venv`, using standard CPython 3.14.4. `requirements.lock` records the tested development environment, including pip 26.3 and setuptools 84.2.1. To reproduce it using Python and pip only:

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv install pip==26.3
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

This machine lacks system `ensurepip`; its existing system pip bootstraps pip into the new environment without installing an OS package. These setup commands download pinned Python distributions deliberately; application startup installs no packages. No apt, Node.js, npm, or JavaScript build tooling is needed.

For a core-only environment, start with a fresh `.venv` and use the following alternative instead of the development setup above. `requirements-core.lock` records the core runtime dependencies, excluding Qt, Jinja2, MCP, and test extras. Install the package without extras; pip build isolation obtains the exact setuptools backend specified in `pyproject.toml` without adding it to the runtime environment:

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv install pip==26.3
.venv/bin/python -m pip install -r requirements-core.lock
.venv/bin/python -m pip install --no-deps .
```

For this configured checkout, run **`./launch-pbdesk`** to open the desktop directly without an editor. It uses the existing `.venv` and connects to the same editor core on **18765**, with `.runtime/editor` for service/credential references and the project-root `pbdesk.sqlite3` database. The matching optional web port is **18766**. Explicit `PBDESK_*` environment overrides are supported. The existing **pbdesk: Desktop** tasks/profiles in VS Code and Zed remain available. A bare `python -m pbdesk.desktop` launch with default settings selects different state/ports.

The desktop probes a signed, authenticated core identity before sending its scoped service credential. If the core is absent, its background worker tries a transient `systemd --user` unit, then a detached foreground-process fallback. Both share the core single-instance guard. A conflicting listener produces an actionable error; the launcher does not kill it. Networking and readiness waits run outside the GUI thread.

Try a sanitized stored-note fixture without a provider account:

```bash
PBDESK_STATE_DIR=/tmp/pbdesk-demo PBDESK_APPLICATION_DIR=/tmp/pbdesk-demo-app .venv/bin/python -m pbdesk.desktop --demo
```

Use a dedicated demo state directory. The fixed source identity is retained, so restarting a demo core with the same state does not add another item or notification claim. `--demo` only affects a core started by that launch; an already running core keeps its existing mode. Demo events use the documented stored-note shape and do not claim to be real incoming SMS.

Notifications supports keyboard navigation, Ctrl+F search, F5 refresh, and a scrollable reading pane with literal titles and safely linked body text. It polls authenticated snapshots every two seconds and preserves the selected item across updates. A live desktop can recover a crashed core through signed discovery and bounded background startup. **Stop all services** advances a persistent launch generation, preventing older clients from reviving it; a fresh desktop/web launch can start the next generation.

## Read, list devices, and send

**Settings → Appearance** offers **System**, **Light**, and **Dark**. System is the default and follows the palette and style delivered by Qt, including KDE integration where available. Changes apply immediately and persist as non-secret settings in the encrypted account database; they do not change global desktop settings. The last main page is also remembered as `main_page` (`notifications`, `sms`, or `summary`); missing or invalid values start at Notifications. Opening Settings or a device dialog never changes it. Reordering the visible rail to Summary first preserves those logical page IDs and the saved startup selection; it does not force startup onto Summary. Merged settings writes preserve `color_scheme`, the page and unknown preference fields. Notifications, SMS, Summary, Settings and activity/review dialogs share the effective palette.

The fixed-width header status stays quiet during ordinary reconnects and refreshes for three seconds, then shows a sustained state. Core unavailability, credential errors, rate limits and actionable decryption/backlog states appear immediately. Click the status or activate it with the keyboard for separate local-core, provider-stream, push-sync timestamp and encryption/key-readiness details. **Live updates** describes the stream, rather than promising complete historical synchronization.

Notification details preserve the full supplied body and newlines, wrap long URLs, and copy the original supplied title/body/URL. Complete HTTP(S) URLs in the body or top-level URL field open only after explicit activation. Unsafe or visibly shortened URLs remain text. Provider HTML and Markdown stay literal; the pane loads no remote resources. The existing normalization limit of **65,536 characters per string** remains. Text omitted or shortened by the source phone/app cannot be reconstructed: Pushbullet documents notification `title` and `body` fields, without promising full source-app content or a required URL field. This does not establish why any particular notification was incomplete. See the official [mirrored-notification API](https://docs.pushbullet.com/#mirrored-notifications).

Supplied notification application names and icons are retained and drawn natively. SMS contact thumbnails supplied as inline base64 images appear beside conversations; missing images use a fallback avatar. Remote contact images, MMS images and file-image attachments require an explicit **Load image** click and open larger native previews. The core resolves a retained account-scoped reference, accepts only supported official HTTPS origins, limits response bytes/formats and decoded image dimensions, and sends no provider authorization, access-token or cookie headers to image hosts. Previews use bounded memory caching, with no disk image cache. An expired image keeps a local Retry control without creating or clearing an unrelated global error. Old mirrored-notification icon metadata discarded by previous versions may remain unavailable until a new event supplies it. Versioned SMS normalization triggers an ordinary paced reread of unchanged threads to recover available media metadata; it does not promise recovery of omitted source content.

Notifications uses a source-device filter for Pushbullet messages and mirrors. SMS independently selects a sending phone and groups stable thread identities with received and sent bubbles in one timeline. Pending requests and their outcomes are clearly marked as local actions. **Message activity** retains all saved actions across every kind, phone and conversation. A missing or incompatible SMS phone stays selected in history-only mode; changing a view never reroutes a saved action. Pushbullet records keep their verified source associations; unidentified records remain unassociated.

Device switching renders previously loaded local conversations immediately, then asks the background worker to read the core cache and queue paced provider synchronization. Last successful sync is separate from refreshing. A stale upstream conversation entry can be reported as unavailable without blocking available history; cached text for that entry is kept. Available-history synchronization is distinct from a complete all-time phone backup. Reading position, selected conversation, and cached text survive ordinary polling, offline state, and decryption errors. Reading, refreshing, switching, and key testing never approve or retry outgoing actions.

**Settings → Devices** displays registrations, capabilities, encryption readiness, and local history in the retained native master/detail view. **Add device** registers this Linux client with its actual returned identity, then offers local encryption configuration; the phone/browser tab guides setup of the Pushbullet client on the same account and Android permissions. **Rename** targets the exact selected registration. **Remove** confirms its name/ID, preserves local history and shared KWallet entries, and keeps saved action destinations unchanged. Active registration does not mean online. An uncertain registration requires refreshing and explicitly reviewing the observed result before another registration attempt.

**Manage encryption** selects the shared `kdewallet → Pushbullet → e2e-password` or a device-specific entry in that same folder. An optional masked password creates a local KWallet entry through the core. **Test saved local decryption** distinguishes key readiness from an authenticated matching ciphertext test; this never configures a phone remotely or uploads a password. The existing `api-key` and shared default entry are preserved.

On Notifications, choose **Compose** or Ctrl+N to create a Note or Link for an explicitly selected active device. On SMS, **New message** or Ctrl+N starts an inline recipient/composer flow; a reply uses the selected conversation only when it has one unambiguous valid E.164 recipient. Group or ambiguous history remains readable. New SMS requires a typed E.164 number with country code; the application never infers a number from a notification title. Link messages require an HTTP(S) URL. **Save for review** or **Review & send** creates a pending action and opens exact review after a successful save in the same context; saving does not send it.

The review window displays the exact device ID/nickname, recipient, title, body, URL, transport, requester, revision, fingerprint, and expiry. Its acknowledgment is initially unchecked. Check it only after reviewing the content, then choose **Approve and send**. Approval is bound to that exact revision and fingerprint. The desktop can edit or cancel pending/queued actions; edits invalidate previous approval and preserve the original expiry. Incoming updates never overwrite an open compose form, and a changed action resets review consent. Review closes when Pushbullet accepts the exact approved revision and fingerprint; historical, failed or uncertain actions do not close it. Accepted local SMS request cards leave the conversation while remaining available in Message activity.

**Pushbullet can process this content. SMS carrier delivery is not end-to-end encrypted.** Outgoing note/link/SMS actions explicitly use `provider_plaintext` over TLS to Pushbullet. The E2E password supports decrypting incoming encrypted mirrored events; it does not provide an undocumented E2E format for outgoing messages. Approval requires a separate acknowledgment of this transport for every action. No automatic downgrade to a different transport occurs.

Message activity distinguishes pending approval, approved/queued, submitting, accepted by Pushbullet, failed, uncertain, expired, and cancelled. **Accepted by Pushbullet is not a delivery receipt.** An uncertain outcome may already have been sent; check the destination before creating another action. The app never blindly retries a send. If creating a pending action times out, the compose dialog keeps its original UUID and exact payload locked; **Reconcile same request** safely asks the core for that same intent instead of creating another one.

## Automatic Summary and reviewed reply drafts

In **Settings → AI**, the editable model dropdown defaults to `gpt-6-luna`. With a configured key, showing AI settings or saving/selecting a key loads available text-model IDs from OpenAI's `/v1/models`; an unavailable catalog never discards the saved custom ID. A listed model is not a guarantee of Responses API support. Choose **Save new API key** to create `kdewallet → Pushbullet → ai-openai-api-key`, or **Use existing saved key** to select that dedicated entry. Creation refuses to overwrite an existing entry. This key is separate from Pushbullet `api-key` and incoming `e2e-password`; no secret is saved in a file or database. Nonsecret model/configuration settings are authoritative in encrypted SQLite; migration archives and removes the selected legacy `ai-settings.json` after verification.

**Summary → Latest** automatically summarizes the account's **active saved preset**. Defaults are **All activity** and **Notifications only**, both covering the past 24 hours. Create/edit presets for notification/SMS sources, cached devices, exact device-and-conversation pairs, a **1/6/24/72/168-hour** window, and an inherited AI Settings model or explicit override. The selected-conversations template requires at least one explicit device/thread pair; an empty selection never silently expands to all SMS. Choices come from local retained data, without provider refresh or cloud calls.

There is one active preset per account, shared across desktop clients. Creating or saving an inactive preset does not activate it or generate a summary. Switching the active preset changes the scope of automatic updates. Editing an active preset uses **Save & apply**, with a disclosure that the next eligible automatic update may send matching cached text to OpenAI. Browsing presets, History or Details does not generate. Only the active preset participates in automatic work; limits remain **100 records**, **24,000 input characters**, a two-second debounce, five-second local status polling and at least **60 seconds between automatic paid attempts**. Work is coalesced/single-flight; empty matching history makes no cloud request. Cached local coverage is not a complete phone-history guarantee.

Latest renders safe Markdown in a native scrollable Qt view with image/resource/embedded-HTML blocking and click-only validated HTTP(S) links. Selection/scroll remain stable through unchanged polls. The last successful result remains visible while updating. **History** supports literal search, preset filters including archived presets, local-calendar date filters, pinned entries and pagination. Archiving a preset retains its history. **Details** shows the captured scope/revision, saved date, original generation date, coverage counts/dates, truncation, model/prompt version, actual available input/output/cached-input tokens, measured latency and reuse metadata. Unknown metrics show unavailable rather than invented zeros; cost is unavailable, with no estimated prices.

Saved outputs/cache and dated history now live in the existing SQLite database. Immutable generated content is deduplicated by account, canonical scope, resolved model, prompt version and input hash; separate history entries preserve each preset revision's captured provenance. Reuse does not rewrite the original dates or scope, and unchanged polls do not append history. A valid old private JSON cache is imported idempotently as legacy history, leaving unknown metrics null. Selected legacy cache and attempt/backoff files are archived inside encrypted migration material before removal; active AI runtime state now remains in the encrypted database.

**History settings** defaults to 30 days, with **7/30/90/365-day** choices. Changing retention previews the count of affected unpinned entries and requires explicit confirmation against the current policy revision. Pins protect entries; generated content is removed only when no retained history entry references it. Account/revision checks reject stale edits. Paid work already in flight keeps the captured old preset/model provenance even if the active preset changes.

On SMS, **AI Draft reply…** opens a compact recipient-focused dialog with collapsed local conversation context and one large editable draft. Opening prepares local context only. One explicit **Draft reply** click refreshes the context and generates a suggestion; edit it in place, then choose **Use draft**. This uses the **current edited text**, confirms replacement of a nonempty composer, closes the dialog and focuses the SMS composer on success. Cancellation, late replies and edits made while a request is pending cannot silently replace user text. Account/device/thread/composer/pending-send guards reject stale insertion. Read-only, group or history-only contexts may produce a copyable draft while Use draft stays disabled with a truthful reason. Nothing automatically creates, approves or sends an action: the normal **Review & send** pipeline remains the only outbound path.

Generation uses the fixed OpenAI Responses endpoint with `store: false`, no tools and no reused conversation. The default Luna request disables reasoning and requests low text verbosity; automatic digest output is capped at 1,000 tokens. Model-specific options are omitted for custom models. See the official [model catalog](https://developers.openai.com/api/docs/models) and [Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create). `store: false` is not a claim about training policy or zero data retention. Live model quality, account access and billing require separate validation.

## Core settings and deliberate restart

**Settings → Core** reads the verified core instance, uptime, coupled/standalone mode, active desktop/web-server counts, core/web addresses, storage paths and lease timings. Addresses, paths, mode and timings are read-only launch-controlled information; this tab does not edit ports, relocate history or change the launch profile.

**Deliver desktop notifications** is an editable saved preference. Choose **Apply notification setting** to persist it in the core store and apply it immediately. The tab distinguishes the requested preference from actual notifier availability/effective delivery state. A missing notification adapter or desktop Do Not Disturb can prevent a visible popup even when delivery was requested. Existing at-most-once notification claims remain in effect.

**Restart core…** is separate from **Stop all services…**. It restarts a quiescent backend while keeping the desktop window/drafts and the same enabled web server process; the replacement core adopts that verified web instance. It refuses while approved actions are ready/submitting or provider mutations are in flight. Refresh status after an uncertain result rather than assuming the restart completed or blindly repeating it. The desktop waits for a different signed core instance; mutation/restart HTTP requests are not automatically replayed.

The restart preserves the old core's exact demo/fixture/standalone mode, original notification launch flag, lease timings and persisted notification override. A private, instance/generation-bound, short-lived handoff coordinates the replacement; it holds no client lease. Restart keeps the Stop All generation, whereas **Stop all services…** advances it and invalidates old clients and pending restart helpers. An older backend needs external activation before this control is available.

## Desktop and web process lifetime

Normal launches use a coupled core. Each desktop and **web server process** holds an opaque lease bound to the signed core instance and launch generation. Leases renew every five seconds, expire after 45 seconds without renewal, and use a three-second final-release grace; a new core allows 20 seconds for its first client. Hiding the desktop to its tray keeps that desktop alive. A full **Exit desktop** releases only its own lease: another desktop or running web server keeps the core alive. When the final client leaves, the coupled core stops after the grace period.

Closing a browser tab does not stop the web server or release its lease. Disable **Settings → Local Web** or stop the server process to release it. A surviving signed web server can be adopted after a core crash without opening a duplicate listener. **Application → Stop all services…** stops web/core and advances the launch generation so old clients cannot recover them; a fresh launch works normally. Login autostart remains off. Explicit `--standalone` core/debug/service operation opts out of client-coupled shutdown. MCP holds no lease and cannot keep a coupled core alive by itself; headless MCP automation needs an explicitly started standalone core.

## Editor launch profiles

VS Code profiles are in `.vscode/launch.json`; choose one in **Run and Debug**.
Zed profiles are in `.zed/debug.json`; run **debugger: start**. Zed uses `.zed`,
not `.zen`. Both editors also have matching run tasks (`.vscode/tasks.json` and
`.zed/tasks.json`) for launching without a debugger: use **Tasks: Run Task** in
VS Code or **task: spawn** in Zed.

The profiles cover the desktop, a sanitized demo desktop, a foreground demo core,
a standalone web status host, and headless non-browser tests. They all use the
existing `.venv/bin/python` and source tree. No profile installs dependencies or
editor extensions. Debug profiles require the editor's Python/debugpy support;
run tasks use the existing environment without a debug adapter.

Editor launches keep development state separate from normal application data:

| Profiles | State directory | Core port | Web port |
| --- | --- | --- | --- |
| Desktop | `.runtime/editor` | 18765 | 18766 |
| Demo desktop, demo core, standalone web | `.runtime/editor-demo` | 18767 | 18768 |

Desktop profiles choose foreground process launching while retaining the normal coupled lifetime. Their debugger covers the desktop process. To debug a persistent backend, **Core (demo, foreground)** explicitly passes `--standalone`; start it before **Desktop (demo)** and stop that standalone core in its own terminal/debug session. The **Web status (standalone)** profile also explicitly passes `--standalone`, allowing an unavailable-core status page without managing a core. Do not run it beside a managed web listener on the same port. These are debug exceptions; normal desktop and `python -m pbdesk.web` launches hold leases.

## Foreground and provider setup

Run a standalone core for development or headless use:

```bash
.venv/bin/python -m pbdesk.cli --foreground --standalone
.venv/bin/python -m pbdesk.cli --foreground --standalone --demo --notifications
.venv/bin/pbdesk-diagnostics
```

The second command enables the optional core-owned KDE notification adapter. The desktop launcher also requests that adapter. The core is the only notification emitter. Persisted at-most-once claims prevent restart duplicates; notification submission does not guarantee a visible popup, particularly with Do Not Disturb enabled.

Open **Settings → Account** to select an existing KWallet entry or save a new Pushbullet access token. The core verifies the token with the official read-only account endpoint before saving the connection reference. New-token setup can create the selected folder in an existing wallet, but never overwrites an existing entry. The token stays in KWallet. A new installation opens this setup page automatically; cancelling keeps it available in Settings without prompting on every poll. A locked established wallet does not become a new installation.

Local CLI setup uses the same authenticated core API:

```bash
.venv/bin/pbdesk-admin provider-token
.venv/bin/pbdesk-admin use-kwallet --wallet kdewallet --folder Pushbullet --entry api-key
```

Use the same application/state directories and ports as the desktop. The commands verify and connect immediately; they do not write a competing JSON configuration. CLI `provider-token` retains the verified system-keyring backend, while the native Account page offers KWallet entry selection/creation. Device encryption and AI setup remain optional, in their existing Settings tabs.

The core reads the selected entry directly using KWallet's session D-Bus interface
and the already installed pure-Python Jeepney dependency. It needs neither Qt nor
dbus-python for this adapter. Allow `pbdesk` access if KWallet prompts. A locked,
denied, or missing selected entry produces a locked provider state; it does not
fall back to another entry or a plaintext file. Demo mode still skips credentials.

For the existing selection, the encryption password is the separate **`e2e-password`** entry in the same **`Pushbullet`** folder of **`kdewallet`**, alongside **`api-key`**. Keep those names and the existing selection. The core reads existing entries locally; the desktop and MCP never receive stored passwords or tokens. A masked new device-specific password may be entered in the desktop and sent once through the authenticated local core API for KWallet storage. After adding or changing the password, choose **Application → Reload provider credentials** to reconnect and retry preserved encrypted events. Encryption status distinguishes unconfigured, ready, locked, and error; retained readable history stays available while keys are unavailable.

To force the desktop launcher's foreground fallback during development:

```bash
PBDESK_LIFECYCLE=foreground .venv/bin/python -m pbdesk.desktop
```

An explicit `pbdesk-admin install-service` operation is available for users who deliberately want a persistent user unit. Normal launches do not install one. Installing a unit does not enable login autostart; enabling it is a separate local decision.

## Optional local web status page

Web hosting is disabled by default. Open **Settings → Local Web** to enable it, inspect its bind address and port, or open the page. The preference is persisted by the core. Disable it to terminate the web listener while keeping the native desktop running.

CLI control uses the same core setting:

```bash
.venv/bin/pbdesk-web status
.venv/bin/pbdesk-web enable
.venv/bin/pbdesk-web disable
```

The page is a Jinja2 hosting smoke test with locally bundled Bootstrap 5.3.8, ordinary navigation, explicit host validation, CSP, and escaped provider status. It exposes service availability only. It has no inbox, login form, browser session, state-changing form, generic proxy, message content, or service credential in the browser. Only loopback is supported. Remote exposure remains deferred until authentication, session security, CSRF protection, HTTPS, revocation, throttling, and a second factor are implemented and verified.

By default, `python -m pbdesk.web` starts/discovers a core and holds a web-process lease. For a debug status-host smoke check that does not manage a core, use `python -m pbdesk.web --standalone`; it can display core-unavailable status. Neither direct launch changes the managed-host preference. Avoid either launch while a managed listener uses the same port.

Vendored assets, upstream source URLs, MIT license, modifications, and SHA-256 hashes are recorded in [the vendor manifest](src/pbdesk/web/vendor-manifest.json). Source-map comments were removed because maps are not packaged. Runtime imports and asset URLs have been inspected for external dependencies. Templates and static files are package data.

## Configuration and MCP

All processes must use the same state directory and port settings:

| Variable | Default |
| --- | --- |
| `PBDESK_STATE_DIR` | `$XDG_STATE_HOME/pbdesk`, or `~/.local/state/pbdesk` |
| `PBDESK_APPLICATION_DIR` | Explicit application/launcher directory; source launches resolve the project directory |
| `PBDESK_CORE_PORT` | `8765` |
| `PBDESK_WEB_PORT` | `8766` |
| `PBDESK_LIFECYCLE` | transient user systemd, with process fallback; `foreground` skips systemd |

The history database `pbdesk.sqlite3` lives in the resolved application directory, beside the application launcher, with an application-wide ownership lock. It is never placed beside the system Python interpreter by accident. Source/IDE launches define this directory explicitly; isolated demos/tests should set both `PBDESK_STATE_DIR` and `PBDESK_APPLICATION_DIR`. Existing plaintext databases require explicit verified SQLCipher migration. A retained state-directory database must be verified as the recorded unchanged ancestor before it can be archived and removed; conflicting histories stop for reconciliation. Unrelated historical backups are preserved and may remain plaintext. See [encrypted history](docs/encrypted-history.md). Core and web bind to `127.0.0.1`; ports must differ. The state directory is private (`0700`), and distinct desktop/web/MCP service tokens are persisted privately (`0600`) and are revocable through `pbdesk-admin revoke-service --role ROLE`. Provider secrets use the verified credential backend rather than these service-token files.

Run the official Python MCP SDK 2.3 adapter through local stdio:

```bash
.venv/bin/python -m pbdesk.mcp_server
```

Configure an MCP client to execute that command, with the same configuration environment as the core. Six narrow tools use authenticated core APIs: `capabilities`, `search_notifications(query)`, `get_item(item_id)`, `list_devices`, `request_action(...)`, and `action_status(action_id)`. `request_action` requires a caller-generated UUID, explicit `provider_plaintext` transport, a selected device, and explicit content/recipient. It only creates a pending action; a person must review and approve it in the native desktop. Reconcile an unknown creation result with the same UUID and original payload. The adapter cannot approve, directly send, edit/cancel actions, run arbitrary SQL/shell/HTTP, or retrieve secrets. It holds no client lease; for headless MCP automation, explicitly run a `--standalone` core with the same configuration. Remote MCP is disabled.

## Tests and current limits

```bash
xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' env QT_QPA_PLATFORM=xcb QT_X11_NO_MITSHM=1 WAYLAND_DISPLAY= MOZ_ENABLE_WAYLAND=0 PBDESK_APPLICATION_DIR=/tmp/pbdesk-tests-app PBDESK_STATE_DIR=/tmp/pbdesk-tests-state PBDESK_BROWSER_TEST=1 .venv/bin/python -m pytest -q
```

Run GUI checks on the private display, leaving the user desktop untouched. The browser test uses explicitly located, already installed Firefox and geckodriver binaries; it never invokes Selenium Manager or downloads a driver/browser. It blocks external HTTP(S) through a dead proxy, allows loopback, checks locally loaded assets, verifies mobile layout, and follows ordinary refresh navigation. Missing existing binaries cause an explicit skip. Some restricted execution sandboxes block asyncio communication primitives and require running tests in the normal host session.

The [initial verification record](docs/verification.md) covers native model/worker tests, web/asset/security tests, the official MCP stdio client, real Firefox local assets/mobile layout, and actual Plasma Wayland checks. The [KWallet follow-up](docs/kwallet-verification.md) records 47 passing non-browser regressions plus approved live account authentication, stored-push synchronization, WebSocket connection, and restart deduplication using the existing wallet entry. A fresh phone-triggered mirrored notification and its real-account desktop popup remain pending.

The prior messaging increment's [verification record](docs/messaging-verification.md) records 88 passing tests including the browser smoke, 87 passing tests with Node absent, actual Plasma Wayland checks of all three send channels against a fake provider, and a clean core-only wheel check without Qt. At that time, the live core was activated: provider connected, encryption ready, five active devices, two SMS-capable devices, and an empty outbox. No real message was sent by the agent. That historical activation predates the Notifications/SMS revision. The current revision has separate guarded activation evidence linked above; the historical account snapshot does not describe its current live state.

The read/device/send increment has focused native tests for device filters, explicit compose validation, exact review consent, stale revisions, outbox state rendering, safe creation reconciliation, and networking outside the GUI thread. The official MCP stdio-client test exercises all six tools against a fake core transport. Tests use sanitized fake providers and never send a real message. Actual account sending requires a separately approved concrete destination and body; a phone-triggered encrypted event remains a distinct live acceptance check.

Current scope leaves rich attachments, persistent compose drafts, user-facing backup/retention tooling, AI, automation, full web authentication/inbox/SSE, remote deployment, and remote MCP for later increments. The native interface currently uses bounded two-second snapshot polling. The web host remains intentionally status-only.

The preceding [redesign verification record](docs/redesign-verification.md) reports **126 passing tests including Firefox**, **125 passing non-browser tests with Node absent**, **17 real worker/core HTTP integration checks using a fake provider**, and **24 independently inspected and hash-verified fictional Wayland/browser captures**. The wheel contains 53 source/resource files byte-identical to the frozen application and runs core imports/migrations with Qt blocked. No installation, real send, device mutation or wallet mutation occurred in those gates. Review the [implemented UI gallery](design/ui-review/implemented/index.html); it includes populated phone conversations, separate Sent history/actions and an explicit history-preservation removal dialog. Live message contents and credential values are excluded from all captures. That preceding increment activated the configured editor core and recorded read-only account verification: **1,020 authenticated/decrypted cached SMS in 56 conversations across two phones**, with successful last-sync times and local reads of 9.50/12.94 ms. Both phone keys verified. Three stale upstream thread entries returned 404 and are explicitly reported as partial availability; available conversations continue syncing and cached history is preserved. This is not a claim of a complete all-time phone backup. The guarded restart preserved all 563 previously cached SMS, seven items and four original ciphertext payloads exactly; credential references and the retained legacy database were unchanged. At that verification point the running outbox was empty and web hosting was off. Details and source evidence are in the [redesign verification record](docs/redesign-verification.md). Use **pbdesk: Desktop** in VS Code/Zed to connect to that editor core.

A fresh phone-triggered encrypted notification remains a separate acceptance check, followed by a user-approved live note or SMS with an explicit device, destination, body, and plaintext-transport acknowledgment. Browser inbox work follows a separately verified authentication/session implementation.

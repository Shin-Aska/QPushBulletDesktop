# pbdesk

A Linux-first Pushbullet desktop for Kubuntu 26.04, KDE Plasma 6 on Wayland, and standard CPython 3.14. One private Python core owns SQLite, provider ingestion, notification delivery, and the optional web-host process. The native interface uses PyQt6 Widgets; no embedded browser, Node.js, CDN, or JavaScript build system is required.

The native application reads stored pushes, mirrored notifications, and synchronized phone SMS conversations; manages Pushbullet device registrations and local decryption references; and sends explicitly addressed notes, links, and SMS through a persistent approval pipeline. Encrypted incoming content is decrypted in the core using local KWallet credentials. Missing or incorrect keys preserve readable history and encrypted recovery records. AI, automations, and browser inbox access remain deferred. See [feature parity](docs/feature-parity.md) and the retained [specification](docs/specification.md).

The sidebar has **Notifications**, **SMS**, and **Devices**. Notifications retains Pushbullet messages and mirrored notifications, with source filtering and search. SMS has its own phone selector, a conversation list, incoming/outgoing chat bubbles and a composer beneath the timeline. Browsing notifications never changes the SMS sending phone. New message starts an inline SMS draft; Ctrl+N follows the current page and Ctrl+F focuses its search.

**Message activity** in the header or Application menu lists every saved Note, Link and SMS action, including failed and uncertain outcomes. Notifications Compose creates Notes or Links. SMS **Review & send** saves a pending action and opens the existing exact review when its account and conversation are still current. Saving sends nothing: approval still requires the displayed destination, revision, fingerprint, expiry and unchecked plaintext consent. Editing a saved SMS action leaves the current conversation draft untouched. Unsaved SMS drafts are local to the running window and its account/phone/conversation; restart persistence is deferred.

The final [Notifications/SMS verification record](docs/notifications-sms-verification.md) reports **150 passing tests including Firefox**, **149 passing tests with Node absent**, **23 real worker/core HTTP checks against a fake provider**, and **13 inspected native Qt captures on private Xvfb/xcb**. Review the [fictional UI gallery](design/ui-review/notifications-sms/index.html). Relaunch the existing **pbdesk: Desktop** profile in VS Code/Zed to load the new frontend; the running core, established root SQLite data profile and KWallet defaults need no restart, installation or migration for this UI revision.

## Run the native desktop

The development environment is `.venv`, using standard CPython 3.14.4. `requirements.lock` records the tested development environment, including pip 26.3 and setuptools 84.2.1. To reproduce it using Python and pip only:

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv install pip==26.3
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

This machine lacks system `ensurepip`; its existing system pip bootstraps pip into the new environment without installing an OS package. These setup commands download pinned Python distributions deliberately; application startup downloads nothing. No apt, Node.js, npm, or JavaScript build tooling is needed.

For a core-only environment, start with a fresh `.venv` and use the following alternative instead of the development setup above. `requirements-core.lock` records the core runtime dependencies, excluding Qt, Jinja2, MCP, and test extras. Install the package without extras; pip build isolation obtains the exact setuptools backend specified in `pyproject.toml` without adding it to the runtime environment:

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv install pip==26.3
.venv/bin/python -m pip install -r requirements-core.lock
.venv/bin/python -m pip install --no-deps .
```

For this configured checkout, launch the existing **pbdesk: Desktop** task/profile in VS Code or Zed. It connects to the editor core on **18765**, uses `.runtime/editor` for service/credential references, and uses the project-root `pbdesk.sqlite3` database. The matching optional web port is **18766**. A bare `python -m pbdesk.desktop` launch with default settings selects different state/ports; use the existing editor profile to open this configured application.

The desktop probes a signed, authenticated core identity before sending its scoped service credential. If the core is absent, its background worker tries a transient `systemd --user` unit, then a detached foreground-process fallback. Both share the core single-instance guard. A conflicting listener produces an actionable error; the launcher does not kill it. Networking and readiness waits run outside the GUI thread.

Try a sanitized stored-note fixture without a provider account:

```bash
PBDESK_STATE_DIR=/tmp/pbdesk-demo PBDESK_APPLICATION_DIR=/tmp/pbdesk-demo-app .venv/bin/python -m pbdesk.desktop --demo
```

Use a dedicated demo state directory. The fixed source identity is retained, so restarting a demo core with the same state does not add another item or notification claim. `--demo` only affects a core started by that launch; an already running core keeps its existing mode. Demo events use the documented stored-note shape and do not claim to be real incoming SMS.

Notifications supports keyboard navigation, Ctrl+F search, F5 refresh, and plain-text item details. It polls authenticated snapshots every two seconds and preserves the selected item across updates. After a disconnect it waits for a restarted core; relaunch the desktop to start a stopped core. This prevents another open window from undoing **Stop all services**.

## Read, list devices, and send

The **Notifications → SMS → Devices** sidebar shares the same core and uses a native slate, jade, and warm-white theme. The top status shows provider connection, encryption state, and the number of encrypted items waiting for decryption. Incoming text is escaped into locally formatted native reading panes; action text stays literal and no view loads remote images.

Notifications uses a source-device filter for Pushbullet messages and mirrors. SMS independently selects a sending phone and groups stable thread identities with received and sent bubbles in one timeline. Pending requests and their outcomes are clearly marked as local actions. **Message activity** retains all saved actions across every kind, phone and conversation. A missing or incompatible SMS phone stays selected in history-only mode; changing a view never reroutes a saved action. Pushbullet records keep their verified source associations; unidentified records remain unassociated.

Device switching renders previously loaded local conversations immediately, then asks the background worker to read the core cache and queue paced provider synchronization. Last successful sync is separate from refreshing. A stale upstream conversation entry can be reported as unavailable without blocking available history; cached text for that entry is kept. Available-history synchronization is distinct from a complete all-time phone backup. Reading position, selected conversation, and cached text survive ordinary polling, offline state, and decryption errors. Reading, refreshing, switching, and key testing never approve or retry outgoing actions.

**Devices** displays registrations, capabilities, encryption readiness, and local history in a native master/detail view. **Add device** registers this Linux client with its actual returned identity, then offers local encryption configuration; the phone/browser tab guides setup of the Pushbullet client on the same account and Android permissions. **Rename** targets the exact selected registration. **Remove** confirms its name/ID, preserves local history and shared KWallet entries, and keeps saved action destinations unchanged. Active registration does not mean online. An uncertain registration requires refreshing and explicitly reviewing the observed result before another registration attempt.

**Manage encryption** selects the shared `kdewallet → Pushbullet → e2e-password` or a device-specific entry in that same folder. An optional masked password creates a local KWallet entry through the core. **Test saved local decryption** distinguishes key readiness from an authenticated matching ciphertext test; this never configures a phone remotely or uploads a password. The existing `api-key` and shared default entry are preserved.

On Notifications, choose **Compose** or Ctrl+N to create a Note or Link for an explicitly selected active device. On SMS, **New message** or Ctrl+N starts an inline recipient/composer flow; a reply uses the selected conversation only when it has one unambiguous valid E.164 recipient. Group or ambiguous history remains readable. New SMS requires a typed E.164 number with country code; the application never infers a number from a notification title. Link messages require an HTTP(S) URL. **Save for review** or **Review & send** creates a pending action and opens exact review after a successful save in the same context; saving does not send it.

The review window displays the exact device ID/nickname, recipient, title, body, URL, transport, requester, revision, fingerprint, and expiry. Its acknowledgment is initially unchecked. Check it only after reviewing the content, then choose **Approve and send**. Approval is bound to that exact revision and fingerprint. The desktop can edit or cancel pending/queued actions; edits invalidate previous approval and preserve the original expiry. Incoming updates never overwrite an open compose form, and a changed action resets review consent.

**Pushbullet can process this content. SMS carrier delivery is not end-to-end encrypted.** Outgoing note/link/SMS actions explicitly use `provider_plaintext` over TLS to Pushbullet. The E2E password supports decrypting incoming encrypted mirrored events; it does not provide an undocumented E2E format for outgoing messages. Approval requires a separate acknowledgment of this transport for every action. No automatic downgrade to a different transport occurs.

Message activity distinguishes pending approval, approved/queued, submitting, accepted by Pushbullet, failed, uncertain, expired, and cancelled. **Accepted by Pushbullet is not a delivery receipt.** An uncertain outcome may already have been sent; check the destination before creating another action. The app never blindly retries a send. If creating a pending action times out, the compose dialog keeps its original UUID and exact payload locked; **Reconcile same request** safely asks the core for that same intent instead of creating another one.

Closing the window hides it only when a system tray is available. **Exit desktop** leaves the shared core and optional web host running. **Stop all services…** confirms that other desktop windows and MCP clients will disconnect, then stops the core and web host. Login autostart remains off.

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

The desktop profiles select the foreground lifecycle. Their debugger covers the
desktop process; to debug backend breakpoints, start **Core (demo, foreground)**
first, then **Desktop (demo)** so it connects to that core. The standalone web
profile uses the same demo core; do not run it alongside a managed web listener
on the same port. Stopping a desktop launch leaves its core running; use
**Application → Stop all services…** when finished, or stop a separately launched
foreground core in its own terminal/debug session.

## Foreground and provider setup

Run a standalone core for development or headless use:

```bash
.venv/bin/python -m pbdesk.cli --foreground
.venv/bin/python -m pbdesk.cli --foreground --demo --notifications
.venv/bin/pbdesk-diagnostics
```

The second command enables the optional core-owned KDE notification adapter. The desktop launcher also requests that adapter. The core is the only notification emitter. Persisted at-most-once claims prevent restart duplicates; notification submission does not guarantee a visible popup, particularly with Do Not Disturb enabled.

Configure a real Pushbullet access token locally using the existing verified Secret Service or KWallet backend:

```bash
.venv/bin/pbdesk-admin provider-token
```

The command prompts for the secret and verifies read-back from the secure backend. It refuses plaintext fallback. Reload provider credentials from the Application menu or restart the core afterward. A locked credential store is a visible provider state; retained local history remains usable. Live-account verification requires an account credential and is separate from sanitized tests.

To reuse an existing KWallet password, select its folder and entry instead. For
the regular editor desktop profile, which uses `.runtime/editor`:

```bash
PBDESK_STATE_DIR="$PWD/.runtime/editor" .venv/bin/pbdesk-admin use-kwallet \
  --wallet kdewallet --folder Pushbullet --entry api-key
```

`Pushbullet` is the KWallet folder; `api-key` is its password entry. Omitting
`--wallet` selects the current network wallet. Setup verifies that the existing
entry can be read, then saves only its location in the selected state directory's
private `provider-credential.json`. It does not move, duplicate, or alter the
KWallet password. Reload provider credentials in that desktop or restart its core, then run **pbdesk: Desktop**. Each state
directory has its own selection; omit the environment override when configuring
the normal command-line application's default state directory.

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

Web hosting is disabled by default. Open **Application → Web interface settings…** to enable it, inspect its bind address and port, or open the page. The preference is persisted by the core. Disable it to terminate the web listener while keeping the native desktop running.

CLI control uses the same core setting:

```bash
.venv/bin/pbdesk-web status
.venv/bin/pbdesk-web enable
.venv/bin/pbdesk-web disable
```

The page is a Jinja2 hosting smoke test with locally bundled Bootstrap 5.3.8, ordinary navigation, explicit host validation, CSP, and escaped provider status. It exposes service availability only. It has no inbox, login form, browser session, state-changing form, generic proxy, message content, or service credential in the browser. Only loopback is supported. Remote exposure remains deferred until authentication, session security, CSRF protection, HTTPS, revocation, throttling, and a second factor are implemented and verified.

For an independently managed foreground status-host smoke check, `python -m pbdesk.web` runs the host directly; it does not enable or persist the core's managed-host preference. Avoid running it while the managed listener uses the same port.

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

The history database `pbdesk.sqlite3` lives in the resolved application directory, beside the application launcher, with an application-wide ownership lock. It is never placed beside the system Python interpreter by accident. Source/IDE launches define this directory explicitly; isolated demos/tests should set both `PBDESK_STATE_DIR` and `PBDESK_APPLICATION_DIR`. Existing state-directory databases migrate by a consistent backup while retaining the source; conflicting histories stop startup for reconciliation. Core and web bind to `127.0.0.1`; ports must differ. The state directory is private (`0700`), and distinct desktop/web/MCP service tokens are persisted privately (`0600`) and are revocable through `pbdesk-admin revoke-service --role ROLE`. Provider secrets use the verified credential backend rather than these service-token files.

Run the official Python MCP SDK 2.3 adapter through local stdio:

```bash
.venv/bin/python -m pbdesk.mcp_server
```

Configure an MCP client to execute that command, with the same configuration environment as the core. Six narrow tools use authenticated core APIs: `capabilities`, `search_notifications(query)`, `get_item(item_id)`, `list_devices`, `request_action(...)`, and `action_status(action_id)`. `request_action` requires a caller-generated UUID, explicit `provider_plaintext` transport, a selected device, and explicit content/recipient. It only creates a pending action; a person must review and approve it in the native desktop. Reconcile an unknown creation result with the same UUID and original payload. The adapter cannot approve, directly send, edit/cancel actions, run arbitrary SQL/shell/HTTP, or retrieve secrets. Remote MCP is disabled.

## Tests and current limits

```bash
xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' env QT_QPA_PLATFORM=xcb QT_X11_NO_MITSHM=1 WAYLAND_DISPLAY= MOZ_ENABLE_WAYLAND=0 PBDESK_APPLICATION_DIR=/tmp/pbdesk-tests-app PBDESK_STATE_DIR=/tmp/pbdesk-tests-state PBDESK_BROWSER_TEST=1 .venv/bin/python -m pytest -q
```

Run GUI checks on the private display, leaving the user desktop untouched. The browser test uses explicitly located, already installed Firefox and geckodriver binaries; it never invokes Selenium Manager or downloads a driver/browser. It blocks external HTTP(S) through a dead proxy, allows loopback, checks locally loaded assets, verifies mobile layout, and follows ordinary refresh navigation. Missing existing binaries cause an explicit skip. Some restricted execution sandboxes block asyncio communication primitives and require running tests in the normal host session.

The [initial verification record](docs/verification.md) covers native model/worker tests, web/asset/security tests, the official MCP stdio client, real Firefox local assets/mobile layout, and actual Plasma Wayland checks. The [KWallet follow-up](docs/kwallet-verification.md) records 47 passing non-browser regressions plus approved live account authentication, stored-push synchronization, WebSocket connection, and restart deduplication using the existing wallet entry. A fresh phone-triggered mirrored notification and its real-account desktop popup remain pending.

The prior messaging increment's [verification record](docs/messaging-verification.md) records 88 passing tests including the browser smoke, 87 passing tests with Node absent, actual Plasma Wayland checks of all three send channels against a fake provider, and a clean core-only wheel check without Qt. At that time, the live core was activated: provider connected, encryption ready, five active devices, two SMS-capable devices, and an empty outbox. No real message was sent by the agent. That historical activation predates the Notifications/SMS revision. The current revision leaves the already running core and account state unchanged.

The read/device/send increment has focused native tests for device filters, explicit compose validation, exact review consent, stale revisions, outbox state rendering, safe creation reconciliation, and networking outside the GUI thread. The official MCP stdio-client test exercises all six tools against a fake core transport. Tests use sanitized fake providers and never send a real message. Actual account sending requires a separately approved concrete destination and body; a phone-triggered encrypted event remains a distinct live acceptance check.

Current scope leaves rich attachments, persistent compose drafts, user-facing backup/retention tooling, AI, automation, full web authentication/inbox/SSE, remote deployment, and remote MCP for later increments. The native interface currently uses bounded two-second snapshot polling. The web host remains intentionally status-only.

The preceding [redesign verification record](docs/redesign-verification.md) reports **126 passing tests including Firefox**, **125 passing non-browser tests with Node absent**, **17 real worker/core HTTP integration checks using a fake provider**, and **24 independently inspected and hash-verified fictional Wayland/browser captures**. The wheel contains 53 source/resource files byte-identical to the frozen application and runs core imports/migrations with Qt blocked. No installation, real send, device mutation or wallet mutation occurred in those gates. Review the [implemented UI gallery](design/ui-review/implemented/index.html); it includes populated phone conversations, separate Sent history/actions and an explicit history-preservation removal dialog. Live message contents and credential values are excluded from all captures. That preceding increment activated the configured editor core and recorded read-only account verification: **1,020 authenticated/decrypted cached SMS in 56 conversations across two phones**, with successful last-sync times and local reads of 9.50/12.94 ms. Both phone keys verified. Three stale upstream thread entries returned 404 and are explicitly reported as partial availability; available conversations continue syncing and cached history is preserved. This is not a claim of a complete all-time phone backup. The guarded restart preserved all 563 previously cached SMS, seven items and four original ciphertext payloads exactly; credential references and the retained legacy database were unchanged. At that verification point the running outbox was empty and web hosting was off. Details and source evidence are in the [redesign verification record](docs/redesign-verification.md). Use **pbdesk: Desktop** in VS Code/Zed to connect to that editor core.

A fresh phone-triggered encrypted notification remains a separate acceptance check, followed by a user-approved live note or SMS with an explicit device, destination, body, and plaintext-transport acknowledgment. Browser inbox work follows a separately verified authentication/session implementation.

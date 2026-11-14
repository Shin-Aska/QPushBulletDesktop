# Verification record — 2026-11-13

This verifies the initial read-only native inbox slice described in the README and
feature-parity matrix. It does not certify the entire retained specification.
All provider payloads were sanitized fixtures or fake HTTP transports. No real
Pushbullet account, outgoing message, AI service, or paid API was used.

## Artifact and environment

- Host: Ubuntu/Kubuntu 26.04.1 LTS, KDE Plasma session, `XDG_SESSION_TYPE=wayland`.
- Standard CPython 3.14.4; Qt 6.12.0; PyQt 6.12.0. The development environment's
  `pip check` passed. The clean installed-wheel environment's `pip check` passed.
- Final wheel: `.runtime/verification/dist/pbdesk-0.1.0-py3-none-any.whl`.
- Wheel SHA-256: `928f9f7938c8349ef1e726db6570358ec53ce86b04c0c3022c5cd7d0b0f6e6f0`.
- The directory is not a Git repository, so a commit ID or dirty-tree status cannot
  be reported. `.runtime/verification/provenance.json` records per-file source
  hashes and lockfile hashes. Every packaged source/asset byte matched the current
  source tree after the checks. No application source or tests were changed by the
  verifier. Build metadata and verification artifacts were generated.
- Full lock SHA-256: `3fe7daca74b5f3d91c3acaeb362c97b18eafcdb4f7858143390d8596ef051901`.
- Core lock SHA-256: `971d4ff4c47e81de98d664e2ac002dd9c8becaf3197c0ae59668e9be6cb22756`.

Only user-authorized pip installations into an isolated temporary venv were used
for clean-install verification. No OS packages, Node tooling, browser, or driver
were installed. Checks requiring loopback sockets, Qt Wayland, or the user session
bus ran with host-session access because the restricted sandbox blocks some
asyncio/socket primitives. This is an execution-environment requirement.

## Automated checks

From the repository root:

```bash
QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 PBDESK_BROWSER_TEST=1 \
  .venv/bin/python -m pytest -q
```

**36 passed in 4.60 seconds; no skips.** This includes storage/migration/FTS,
persisted source deduplication and notification claims, role scopes/revocation,
signed identity tampering and wrong listeners, lifecycle failure paths, bounded
SSE replay/slow-write disconnects, malformed provider payloads and timestamps,
rate-limit handling, concurrent persisted web toggles, native model/worker checks,
web host validation/escaping/CSP/assets, official Python MCP stdio round trips,
and the installed Firefox smoke test.

The browser used existing Firefox/geckodriver binaries under
`/snap/firefox/current/usr/lib/firefox`. Its dead external HTTP(S) proxy allowed
only loopback; resource timing entries were all on the local web origin. Both
Bootstrap assets loaded, a computed style was checked, the 390-by-844 viewport did
not overflow horizontally, and ordinary Refresh status navigation worked. Selenium
Manager and browser downloads were not used.

```bash
PATH=/mnt/internal-hdd/Development/pushbullet/.venv/bin \
QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 \
/mnt/internal-hdd/Development/pushbullet/.venv/bin/python \
  -m pytest -q -m 'not integration'
```

**35 passed in 1.82 seconds; one browser test deliberately deselected.** A separate
`shutil.which` assertion under this exact PATH found no `node`, `nodejs`, `npm`, or
`npx`. The official Python MCP client completed initialize/list/call validation and
all three read-only tools in this environment. No Node inspector was needed.

## Build and clean installation

```bash
.venv/bin/python -m build --wheel --no-isolation --outdir .runtime/verification/dist
.venv/bin/python -m venv --without-pip /tmp/pbdesk-core-verify-20261113
.venv/bin/python -m pip --python /tmp/pbdesk-core-verify-20261113 \
  install --only-binary=:all: -r requirements-core.lock \
  .runtime/verification/dist/pbdesk-0.1.0-py3-none-any.whl
```

The wheel built successfully and contains 43 files, all seven console entry points,
Alembic environment/revision files, Jinja templates, application static assets,
Bootstrap CSS/bundle JS, the Bootstrap MIT license, and the vendor manifest.
Every manifest asset hash was verified against the actual wheel bytes. The complete
inventory is in `.runtime/verification/wheel-inspection.json`.

The generated `core_only_acceptance.py` was executed using the temporary venv's
Python with working directory `/tmp`. `pbdesk.__file__` resolved to that venv's
`site-packages`, outside the checkout. PyQt6, Jinja2 and MCP were all absent.
The installed wheel started a real foreground demo core, became ready after
migrations, returned exactly one note at sequence 1, and reported web disabled.
Diagnostics reported FTS5 available, provider state `demo`, and a verified
`keyring.backends.SecretService` backend. All three optional distributions were
reported as not installed. The core shut down successfully. Evidence:
`.runtime/verification/core-only-results.json`.

After preserving the core-only evidence, only `Jinja2==3.1.6` was added to that
temporary environment through pip. From `/tmp`, `wheel_web_acceptance.py` rendered
an installed-wheel Jinja status page and retrieved all four local CSS/JS assets
with HTTP 200. Manifest hashes matched, unknown Host was rejected with 400, and
POST to the status page was rejected with 405. Qt remained absent. Evidence:
`.runtime/verification/wheel-web-results.json`. Starlette emitted its upstream
httpx TestClient deprecation warning; rendering and assertions passed. No extra
HTTP-client package was installed to silence the warning.

## Actual native Wayland and notification acceptance

The generated harness can be inspected at
`.runtime/verification/native_acceptance.py`. It used actual `QApplication`,
`CoreWorker` and `MainWindow` with `QT_QPA_PLATFORM=wayland`, fresh isolated state,
random loopback ports, and the documented foreground lifecycle mode.

```bash
.venv/bin/python .runtime/verification/native_acceptance.py
```

Observed outcomes:

1. The native worker automatically started the missing core with `--demo`; no
   separate manual backend launch was needed. The GUI timer continued firing
   during startup (81 ticks before both clients were established).
2. The authenticated snapshot contained exactly one documented sanitized stored
   note, source `stored:pbdesk-sanitized-demo-v1`, local ID 1, event sequence 1.
   The real native list and detail view displayed it. Only the app window was
   captured with `window.grab()`; the screenshot was inspected and is saved as
   `.runtime/verification/native-wayland.png`.
3. A second native worker/window connected to the same signed core instance;
   item count stayed one. No additional core or provider listener was created.
4. Web was disabled initially. Enabling through the same core API used by the
   settings UI started the Jinja host and served local Bootstrap. Exiting both
   desktops left the core and web host alive. Disabling web closed its listener
   while the core stayed ready.
5. Explicit shutdown stopped the core. A fresh native worker restarted it using
   the same state. The instance UUID changed; item ID, item count and sequence
   remained 1. No extra retained item or event appeared.
6. A scoped PATH wrapper recorded the core notifier's invocation and called the
   actual existing `/usr/bin/notify-send`. There was exactly **one actual submission**,
   with return code 0, across the initial launch and restart. Its title/body were
   the sanitized demo fixture. Persisted notification claiming prevented a repeat.
7. All verification core/web processes were shut down afterward. Worker errors
   recorded during this acceptance run: none.

Evidence is in `.runtime/verification/native-results.json` and
`.runtime/verification/notify-submissions.jsonl`. The native harness uses a fresh
state directory; remove/archive its generated state and submission log before
intentionally repeating it, or select a new isolated evidence directory.

Notification service acceptance is verified; a visible popup is **not** verified.
The actual KDE notification service reported `Inhibited = true` (Do Not Disturb).
That setting was read only and was not changed. At-most-once claims also mean a
crash or failed notification service after claiming may lose a popup; guaranteed
visible delivery is not claimed.

## Actual preferred systemd lifecycle

```bash
.venv/bin/python .runtime/verification/systemd_acceptance.py
```

With `PBDESK_LIFECYCLE` unset, actual `ensure_core` created a transient user unit.
`systemctl --user show` reported `ActiveState=active`, `SubState=running`,
`Transient=yes`, a positive MainPID, and a fragment under
`/run/user/1000/systemd/transient/`. Repeated discovery returned the same signed
instance. This used the already deduplicated demo state, so the note and sequence
remained 1 and no new notification claim was created. Authenticated shutdown left
the transient unit inactive, and cleanup stopped it. No persistent service unit
was installed or enabled. Evidence: `.runtime/verification/systemd-results.json`.

## Limits and unverified behavior

- Real Pushbullet credentials and account events were unavailable. Live account
  REST/websocket recovery, actual provider rate limits, and a documented real
  account event through to the native inbox remain unverified. Demo/fake-provider
  verification cannot substitute for that acceptance run.
- Encryption/decryption, sending/replies, approvals/outbox, drafts/conflicts, AI,
  automations, full browser accounts/session expiry/CSRF/inbox/SSE, remote hosting,
  remote MCP, retention and automated backup/restore are deliberately deferred.
  Tests do not imply these workflows exist.
- Real Wayland GUI and core/web lifetimes were exercised separately from headless
  Qt tests. Native settings-dialog manual interaction, tray interactions, and a
  physical user observation of a popup were not separately verified.
- The actual KDE service accepted one sanitized notification. DND visibility and
  crash-safe guaranteed delivery remain outside the observed result.

The next acceptance milestone is a read-only, real-account documented Pushbullet
event on this target session, followed by core-owned explicit-destination
approval/outbox work before message composition is enabled.

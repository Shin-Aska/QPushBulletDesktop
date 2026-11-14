# Read, devices, and approved sending — verification

Verified on 2026-11-14 (Asia/Manila) against the final integrated application.
This record covers independent fixture-based verification. It does not report a
real message sent or a real phone-triggered encrypted notification received.
No real credentials, account history, devices, or message contents were read by
this verifier. The existing editor core on ports 18765/18766 was left running and
was not restarted or used by these checks.

## Results

| Check | Observed result |
| --- | --- |
| Complete pytest suite, installed browser enabled | **88 passed in 8.38s**, no skips |
| Node-absent non-browser suite | **87 passed in 4.15s**, one browser test deliberately deselected |
| Python compilation | `compileall` passed for `src` and `tests` |
| Dependency consistency | Main venv and isolated core-only venv `pip check` passed |
| Native Qt Wayland with real loopback API and fake upstream | Inbox/decryption/devices/compose/review/outbox/restart checks passed |
| Final wheel installation outside checkout without Qt/Jinja/MCP | Import, crypto vector, migration/history/FTS and fake approved dispatch passed |
| Wheel contents and provenance | New modules/revision, seven entry points and web assets present; vendor checksums and source bytes matched |

The final wheel is
`.runtime/messaging-verification/dist/pbdesk-0.1.0-py3-none-any.whl`.
Its SHA-256 is
`cf96b61fe60b390f5ea91610e5a8e9f9043144e330109ecbae184b01c11c975e`.
The wheel contains 51 files. Packaged application source and asset bytes matched
the final source tree. The workspace is not a Git repository, so no source commit
or dirty-tree status can be reported. Per-file and lockfile hashes are retained in
`.runtime/messaging-verification/provenance.json`.

## Reproducible checks

Commands ran from `/mnt/internal-hdd/Development/pushbullet` unless noted.
Socket/subprocess/Wayland checks required host-session execution because the
restricted sandbox blocks some asyncio/socket primitives. Calls used bounded
50-second outer timeouts where applicable.

```bash
timeout 50s env QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 \
  PBDESK_BROWSER_TEST=1 .venv/bin/python -m pytest -q

timeout 50s env PATH=/mnt/internal-hdd/Development/pushbullet/.venv/bin \
  QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt6 \
  /mnt/internal-hdd/Development/pushbullet/.venv/bin/python \
  -m pytest -q -m 'not integration'

.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pip check
```

A separate `shutil.which` assertion under the restricted PATH found none of
`node`, `nodejs`, `npm`, or `npx`. The no-Node suite includes the official Python
MCP stdio client and all six exposed tools. It does not require a Node inspector.

The browser test used existing Firefox/geckodriver binaries. Its dead proxy
blocked external HTTP(S), with loopback bypassed. Local Bootstrap CSS and bundle
JS loaded, resource entries stayed on the local web origin, the mobile viewport
fit without horizontal overflow, and ordinary Refresh status navigation worked.
No browser, driver, Node package, OS package, or new project dependency was installed.

Automated coverage includes exact action revisions/fingerprints and plaintext
acknowledgment, role scopes including MCP's inability to self-approve, idempotent
request UUIDs, editing/cancellation/expiry, atomic claiming and restart recovery,
uncertain outcomes without automatic retries, device revalidation before sends,
cancellation after durable dispatch claim, malformed provider input, encryption
authentication and retained-ciphertext recovery, the retained cross-language crypto
vector, existing storage/SSE/security/lifecycle regressions, native controls and
worker threading, web security/assets, and official MCP protocol behavior.

## Actual native Wayland acceptance with fake upstream

```bash
timeout 50s .venv/bin/python \
  .runtime/messaging-verification/native_fake_acceptance.py
```

The harness creates a fresh private state directory and random loopback ports,
starts a real Uvicorn/FastAPI core, and runs actual `QApplication`, `CoreWorker`,
`MainWindow`, `ComposeDialog`, and `ReviewDialog` objects. Qt reported `wayland`;
this is separate from the offscreen pytest checks. GUI code was run from the
source tree, whose packaged bytes were subsequently checked against the final wheel.

The injected provider subclasses the production provider, retaining its device
normalization and outgoing payload construction. Its only HTTP transport is
`httpx.MockTransport`; its run/reload methods use fixture data and an in-memory
fixture key. The real token reader is replaced with a function that raises if
called. No real WebSocket, KWallet entry, provider HTTP connection, or notification
submission is used. No real upstream acceptance or delivery is implied.

Observed sequence:

1. An ordinary sanitized stored note appeared in the native inbox. An encrypted
   fixture mirror was retained with pending count 1 before a key was available.
2. The worker's Reload provider command used the real API to load the fixture key
   and retry decryption. Pending count became 0 and encryption state became ready.
   The decrypted mirror appeared in the native list/detail view. Literal `<b>`
   text remained plain text. The mirror plus ordinary note produced two retained
   items at event sequence 2.
3. Three cached fixture devices appeared. Filters returned three total devices,
   two active push devices, one active SMS device, and one inactive device.
   SMS composition offered only the active SMS-capable fixture phone and began
   with an empty recipient. A phone number was typed explicitly by the harness.
4. Notes, links and SMS were composed through actual native forms. Each Save to
   Outbox created a pending action and produced **zero upstream POSTs**. Each
   review initially had unchecked consent and a disabled Approve and send button.
   Device ID, body, fingerprint, and plain-English transport warning matched the
   action. The harness checked consent and clicked the approval button.
5. Each approved note/link/SMS produced exactly one fake upstream POST and reached
   `upstream_accepted`. The production provider produced `/v2/pushes` for note/link
   and `/v2/texts` for SMS, with the explicit fixture device, E.164 address, and a
   UUID SMS guid. The link URL was preserved exactly. These are simulated provider
   responses, **not actual Pushbullet acceptance or delivery receipts**.
6. An action was edited through the real API while its review was open. Revision
   increased, fingerprint changed, expiry stayed fixed, and old consent cleared.
   Cancellation through the review button produced no upstream POST.
7. Another approved fixture action received a simulated HTTP 503. It became
   uncertain. Multiple dispatcher passes did not retry it.
8. Clients and core stopped; the core restarted with the same isolated state.
   The instance UUID changed, the same two inbox records remained at sequence 2,
   and all five actions persisted. The uncertain action remained uncertain, with
   no retry after restart. Total fake POST count stayed four: the three successful
   channel fixtures and the intentionally ambiguous fixture. Worker errors: none.

The first harness attempt stopped on a verifier assertion that still expected the
raw `provider_plaintext` enum in the preview. The final UI deliberately uses a
plain-English warning. Only the generated harness expectation was corrected; no
application source or tests were changed. The corrected full run passed.

Evidence: `.runtime/messaging-verification/native-results.json` and the generated
harness. Only the fixture application windows/dialogs were captured, using Qt's
widget capture. All five screenshots were visually inspected:

- `inbox-decrypted-wayland.png`
- `devices-wayland.png`
- `compose-sms-wayland.png`
- `review-sms-unchecked-wayland.png`
- `outbox-wayland.png`

All reside under `.runtime/messaging-verification`. All harness core threads and
native workers were stopped after verification. Rerunning the harness creates a
new isolated state directory; it does not reuse the production editor state.

## Final wheel and clean core-only compatibility

```bash
.venv/bin/python -m build --wheel --no-isolation \
  --outdir .runtime/messaging-verification/dist
.venv/bin/python -m venv --without-pip /tmp/pbdesk-messaging-core-verify
.venv/bin/python -m pip --python /tmp/pbdesk-messaging-core-verify install \
  --no-cache-dir --only-binary=:all: -r requirements-core.lock
.venv/bin/python -m pip --python /tmp/pbdesk-messaging-core-verify install \
  --no-deps .runtime/messaging-verification/dist/pbdesk-0.1.0-py3-none-any.whl

cd /tmp
timeout 50s /tmp/pbdesk-messaging-core-verify/bin/python \
  /mnt/internal-hdd/Development/pushbullet/.runtime/messaging-verification/core_wheel_acceptance.py
```

The previous temporary verification venv no longer existed, so a fresh isolated
venv was created using only the existing exact core pins and authorized pip-only
installation. The main venv and global packages were not modified.

`pbdesk.__file__` resolved to the temporary venv's `site-packages` while running
outside the checkout. PyQt6, Jinja2, and MCP were absent. The installed wheel:

- Passed the retained PBKDF2/AES-GCM known-vector check.
- Created a database at frozen migration `0001`, inserted fixture history and a
  notification claim, then upgraded through installed revision `0002` on core
  startup. History stayed searchable through FTS and the notification claim stayed
  set. The new device/actions/audit/locked-envelope tables were present.
- Served devices and capabilities through its API; pending creation made no send,
  repeating the exact request UUID returned the same action, and matching explicit
  approval produced exactly one fake send and an accepted outbox record.
- Kept web hosting disabled by default.

The wheel inventory includes both migration revisions, encryption/actions/outbox
modules, all seven console entry points, Jinja templates, Bootstrap CSS/bundle,
application CSS/JS, the vendor manifest and the Bootstrap license. Every vendored
asset hash matched the packaged bytes. Details are in `wheel-inspection.json` and
`core-wheel-results.json` under the evidence directory.

Starlette emitted its existing httpx TestClient deprecation warning during the
clean-wheel check. Assertions passed. No additional HTTP-client dependency was
installed to silence that warning.

## Remaining live acceptance boundaries

This verifier did not read `kdewallet/Pushbullet/api-key` or `e2e-password`, contact
the real account, send a real note/link/SMS, or capture real history. Parent-run
approved live account checks are attributed in the following section. A fresh
real encrypted notification shown in the native inbox and a separately approved
concrete live send remain distinct from these fixtures.

Incoming fixture E2E decryption does not establish E2E confidentiality for outgoing
notes, links, or SMS. Outgoing actions explicitly use provider-readable content
over HTTPS; the native warning and per-action consent were verified. Provider
acceptance is not delivery confirmation. Actual receiving-device behavior,
carrier delivery, live provider failures, visible KDE popups, and phone-triggered
E2E interoperability were not verified here. Browser inbox/accounts, remote
hosting/MCP, AI, automation, persistent compose drafts, and full incoming SMS
conversation history remain outside this increment.


## Parent-run approved live activation

The parent agent performed the following authorized live activation after the
independent fixture and package checks above. This verifier read only its safe
aggregate [live-results.json](../.runtime/messaging-verification/live-results.json)
and the parent's handoff; it did not independently access account credentials,
provider traffic, or production history. These observations supplement the
fixture results without changing their scope or the final wheel hash.

The parent stopped the old core. An initial cold backup-lock attempt returned
`AlreadyRunning` while the old listener had closed but shutdown still held the
ownership lock. The parent waited for lock release before taking the private
backup; no overlapping core or premature backup was reported. The backup at
`.runtime/editor-backups/20261114-020049` was created with mode `0700` and retained
schema revision `0001` with one history item. Starting the updated core migrated
that state to `0002`; one visible item remained and duplicate source-ID count was
zero.

The parent verified signed readiness and reported:

- Core ready, provider connected, encryption state ready, encrypted pending 0.
- Five cached devices, all active; two SMS-capable devices.
- Send readiness true; outbox action count 0.
- Zero live actions created or approved by the agent.

The existing `kdewallet` wallet, `Pushbullet` folder, `api-key` token entry, and
`e2e-password` entry stayed unchanged. The password was read and derived locally
by the core; it was not printed or transmitted. Encryption readiness establishes
successful local key setup, **not interoperability with a fresh encrypted event
from a real device**.

The updated core was intentionally left running for the regular editor state at
`.runtime/editor`, with configured core/web ports 18765/18766. These configured
ports do not imply that optional web hosting is enabled. No real outgoing message
or fresh phone-triggered encrypted frame was verified during this activation.
A user-approved concrete send and a fresh real encrypted notification displayed
in the native inbox remain outstanding live acceptance checks.

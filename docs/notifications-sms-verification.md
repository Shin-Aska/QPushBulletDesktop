# Notifications and SMS revision verification

This revision replaces the desktop Inbox/Outbox navigation with Notifications, SMS and Devices. Notifications keeps the retained Pushbullet/mirrored-notification reader; SMS owns an independent phone context, conversation list, incoming/outgoing bubble timeline and inline composer. Every saved action remains accessible through Message activity. The core API, database, provider synchronization, KWallet setup and status-only web architecture are unchanged.

## Implementer checks

On 2026-11-14, the existing environment ran:

```bash
QT_QPA_PLATFORM=offscreen PBDESK_APPLICATION_DIR=/tmp/pbdesk-shell-app PBDESK_STATE_DIR=/tmp/pbdesk-shell-state .venv/bin/python -m pytest tests/test_desktop.py tests/test_desktop_actions.py tests/test_desktop_chat.py -q
```

Observed result: **47 passed in 3.09s**. This was a focused integrated check during implementation before the independent final review/capture source freeze, not a claim of full regression completion. It covered exact sidebar identity, original snapshot forwarding, independent notification/SMS contexts, complete Activity access and selection, exact consent/revision/fingerprint review, explicit destination validation, stale-account create rejection before POST, worker metadata separation, same-UUID reconciliation, cached history on errors, per-conversation drafts, stale results and non-GUI networking. The SMS component worker separately reports its own focused cases; independent final results belong below.

After the final shell visibility/callback guards and component appearance repairs, the focused integrated suite was repeated on a private Xvfb display:

```bash
xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' env QT_QPA_PLATFORM=xcb QT_X11_NO_MITSHM=1 WAYLAND_DISPLAY= MOZ_ENABLE_WAYLAND=0 PBDESK_APPLICATION_DIR=/tmp/pbdesk-shell-app PBDESK_STATE_DIR=/tmp/pbdesk-shell-state .venv/bin/python -m pytest tests/test_desktop.py tests/test_desktop_actions.py tests/test_desktop_chat.py -q
```

Observed result: **49 passed in 3.19s**. The private display and its temporary authorization were created and cleaned up by `xvfb-run`; no user desktop windows were opened.

The capture helper is [capture_notifications_sms.py](../design/ui-review/capture_notifications_sms.py). It creates a fake worker and temporary application/state directories before constructing production Widgets. Its no-op worker records commands without contacting a core, credential store or provider. Initial implementation inspection used native Wayland; after the user requested isolated GUI checks, the final capture path uses the already installed `xvfb-run` with a private authenticated X display and Qt `xcb`. No packages or dependencies were installed. The helper rejects an interactive desktop platform and requires an explicit private-display flag for `xcb`. It flushes deferred Qt widget deletion before grabbing, so switching to New message cannot leave a previous thread's disposed bubbles in a screenshot.

The new [gallery](../design/ui-review/notifications-sms/index.html) contains **13 actual native Qt states on private Xvfb**: Notifications, received/sent SMS conversation, reply draft, pending request, uncertain request, new message, new message draft, all-action Activity, exact review, advanced revision/fingerprint review, uncertain review, Devices, and Note/Link composition. [manifest.json](../design/ui-review/notifications-sms/manifest.json) records the observed platform, image dimensions/hashes and source hashes. Each capture checks the fictional SMS phone, at least three nonempty conversation threads, and five stored messages before and after capture. Populated conversation captures also assert both received and sent body text; New message captures reject previous-thread body text. This prevents wrong-device/empty-history and stale-widget fixture failures. All names/numbers/text are fictional. Existing historical galleries/manifests remain retained.

After the component's appearance changes stabilized, the final private Xvfb capture run completed with platform `xcb`. The implementer verified all 13 image hashes and all six production source hashes against the manifest. Visual inspection of the populated, uncertain, New message and new-draft captures confirmed the intended layout and no stale previous-thread bubbles. The independent verifier subsequently repeated the capture run and inspected all 13 images; final results follow below.

Implementation runs and screenshots do not approve any actual message. No live SMS, device, wallet or shared-core lifecycle mutation was performed for this revision. The existing editor core and root application database were left running and untouched by these checks.

## Independent final gates

The independent [final evidence report](../.runtime/notifications-sms/final-verification.json), recorded at **2026-11-14T08:41:48.380317+00:00**, reports all gates passed against unchanged application sources:

| Gate | Observed result |
| --- | --- |
| Complete regression suite with installed Firefox | **150 passed in 10.06s**, zero failures, errors or skips |
| No-Node client PATH suite | **149 passed in 7.21s**, one browser-marker test deselected; Node did not resolve in the Python client's PATH |
| Actual CoreWorker/MainWindow/chat/Activity through authenticated HTTP into the production CoreAPI, with a fictional provider and temporary state | **23 checks passed** |
| Fresh private Xvfb/xcb capture run | **13 screenshots**, all independently inspected; all image hashes and six production source hashes matched |
| Built wheel, without isolation or installation | **55 source/resource files byte-identical** to the frozen sources, including chat/activity; extracted core imports and migrations passed with Qt blocked |

The HTTP checks exercised exact inline creation, initially unchecked review with no send, one explicitly approved fake SMS, phone/navigation/cache/draft/scroll-anchor independence, Note and SMS access through Activity, truthful partial history availability, account-boundary cache clearing, stale SMS rejection and rejection of a queued prior-account create before POST. Provider acceptance remained distinct from delivery. This used fake provider operations only; it did not validate real phone delivery.

The verifier ran these exact private-display wrappers:

```bash
xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' env QT_QPA_PLATFORM=xcb QT_X11_NO_MITSHM=1 WAYLAND_DISPLAY= MOZ_ENABLE_WAYLAND=0 PBDESK_CAPTURE_PRIVATE_DISPLAY=1 .venv/bin/python -u .runtime/notifications-sms/verify_final.py
xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' env QT_QPA_PLATFORM=xcb QT_X11_NO_MITSHM=1 WAYLAND_DISPLAY= MOZ_ENABLE_WAYLAND=0 PBDESK_CAPTURE_PRIVATE_DISPLAY=1 .venv/bin/python -u design/ui-review/capture_notifications_sms.py
```

The runner sets fresh temporary `PBDESK_APPLICATION_DIR` and `PBDESK_STATE_DIR` values before its subprocesses. It enables the installed Firefox test explicitly and clears PATH only inside the no-Node Python client run, leaving the wrapper's existing system utilities available. The final captures were recorded at **2026-11-14T08:40:31.288033+00:00**. Both wrappers returned success and cleanup left no Xvfb processes. No existing user DISPLAY was reused and no final-source Wayland capture is claimed. The verifier inspected all images, including the fresh empty New message/new-draft timelines.

| Artifact identity | SHA-256 |
| --- | --- |
| Frozen application source set, 55 files | `9d0f7d7cb6995f6d663b15ea3c57894dd68cab4d6e5939e20e3d7263e614fdb2` |
| Built wheel | `7413357631b7e637121e150147250910149561524588abf61cbb30b2855b8820` |
| Final capture manifest | `f4d56ac13d99ff6b3831bd5b106cd9f7fdbf8646f8ef9b8fb79d049eed2109e9` |

Source identity remained unchanged through the complete suite, no-Node suite, HTTP checks, package verification and final captures. The final report links the exact commands, XML, logs, source inventory, integration report and package evidence in `.runtime/notifications-sms/`. Documentation was then updated to record those outcomes; production sources and verified artifacts were not changed.

The existing visible desktop, its drafts, the running editor core and production database were not read or modified by the final verifier. No installation, real provider/wallet operation, real SMS, device mutation or service restart occurred. Relaunch the existing **pbdesk: Desktop** profile in VS Code/Zed to load the new frontend; keep the established data profile, root SQLite database and KWallet defaults. No shared-core restart or new installation is required. A live send or phone delivery check remains outside this fake-provider verification.

## Accepted SMS request display correction

On 2026-11-14, a follow-up fix removes `upstream_accepted` SMS request cards from the conversation. Phone history supplies the incoming/outgoing message bubbles; every accepted request remains available in Message activity, including requests whose phone history has not arrived. Acceptance still does not imply carrier delivery. Other request states remain visible, and no body/time comparison is used to infer delivery or merge messages.

If the selected local-request thread becomes empty, the page keeps the recipient context: it selects a unique matching phone-history thread when available, or opens New message for the same valid recipient. Existing recipient and conversation drafts are preserved.

The [follow-up verification report](../.runtime/sms-request-cards/verification.json) records **26 focused SMS tests passed** and **159 complete regression tests passed**, including the installed Firefox case, on private Xvfb/xcb with temporary application/state directories. The regressions cover accepted-card removal, complete Activity retention, pending/queued/submitting/failed/uncertain visibility, missing phone history, recipient focus and draft preservation. The [capture manifest](../.runtime/sms-request-cards/capture-manifest.json) records three new native Qt captures with fictional fixtures: pending request, [accepted send](../.runtime/sms-request-cards/accepted-send.png), and accepted Activity. The latter two were visually inspected. Historical redesign reports and galleries above remain separate.

No packages were installed and no live messages, credentials, shared database, core service or visible desktop windows were used for this correction. Relaunch **pbdesk: Desktop** to load it.

## Review dialog closes after an accepted send

The review dialog now closes automatically when a send approved in that dialog reaches `upstream_accepted`. It stays open while queued/submitting and on failed, uncertain or rejected outcomes. Reviewing an older accepted action in Message activity remains open for inspection. Automatic closure is tied to the approved revision and fingerprint; the main SMS window stays open and the Activity record remains available.

The [verification report](../.runtime/review-auto-close/verification.json) records **34 focused desktop-action tests passed** and **166 complete regression tests passed**, including installed Firefox, on private Xvfb with temporary state and fake send results. Coverage includes asynchronous acceptance, immediate acceptance in the approval response, failure/uncertainty/error handling, historical inspection and revision changes. No installation, real message, credential access, live core restart or visible desktop interaction was performed. Relaunch **pbdesk: Desktop** to load the change.

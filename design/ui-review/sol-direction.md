# Sol visual direction: slate and jade

Independent target-product visual proposal for **pbdesk**, prepared for Mr Orilla's finalization. These are generated raster mockups, not screenshots of an implemented redesign. Actual application screenshots remain in `current/`. No app source, runtime theme, transport, credentials, live data, registration, or device removal was changed by this visual work.

## Direction

A deep slate navigation rail, warm-white working surfaces, jade selections/actions, and restrained amber/red state colors give pbdesk a calm native Linux identity. The former magenta came from the current system theme; it was not treated as a brand requirement. Native tables, splitters, dialogs, menus, and system-style typography keep the design feasible in QtWidgets.

Main navigation is **Inbox → Outbox → Devices**. Both message pages have device context and Pushbullet/SMS views. Message text and the exact destination take priority; fingerprints and revision metadata belong in Advanced details. The lowercase pbdesk name remains; the paper-plane mark is a proposed visual treatment.

Full SMS conversations, history, and incremental synchronization are target scope. The chosen Inbox now illustrates the product being built, with useful SMS conversations, instant local reading, last successful sync, and quiet ongoing refresh. Earlier capture-only and not-supported product copy was rejected and is superseded. Backend research belongs in technical notes, not normal product copy.

## Selected review images

| Selected file | Purpose |
| --- | --- |
| `proposed/01-inbox-slate-jade-v5.png` | Current target: paired Pushbullet/SMS Inbox, device selectors, populated SMS conversation, cached-first reading and background sync |
| `proposed/02-devices-slate-jade-v2.png` | Current target: Add/Refresh, registered devices, selected-device capabilities, local E2E/key source, Rename/Remove, local history |
| `proposed/03-outbox-slate-jade-v2.png` | Earlier **pending-action and approval reference**, with Pushbullet destination/SMS sending-phone filters; not yet a complete phone Sent-history design |
| `proposed/04-compose-and-edit-slate-jade-v2.png` | Note, Link, SMS, and Edit reference |
| `proposed/05-review-and-outcome-slate-jade.png` | Exact-payload review, unchecked/checked acknowledgement, uncertain outcome reference |
| `proposed/06-settings-and-service-controls-slate-jade.png` | Web setting variants, Stop all, Application/tray menus reference |
| `proposed/07-web-status-responsive-slate-jade.png` | Desktop/narrow status-only web companion reference |
| `proposed/08-empty-and-connection-states-slate-jade-v2.png` | Empty Inbox and retained reading during provider interruption reference |
| `proposed/09-device-management-flows.png` | Current target: Linux registration, phone/browser setup, local KWallet encryption selection/test, precise removal |

The latest three deliverables are Inbox v5, Devices v2, and companion 09. Other older boards are retained references; their superseded navigation/count details do not override the accepted requirements. In particular, **target SMS Outbox includes the selected phone's Sent history alongside clearly separated pending pbdesk actions**. The selected Outbox image illustrates those actions and their controls only. An action ledger must not be relabeled as complete phone Sent history.

Originals and all intermediate image versions remain archival. `prompts.json` contains exact successful prompts, reference images, default generated-image source paths, selected outputs, and selected roles. All images used the built-in image generator; no fallback API/CLI or installation was used.

## Accepted experience and local data

The authoritative user requirements are in [accepted-requirements.md](accepted-requirements.md). These notes and images specify intended behavior; they do not create a database or implement ingestion.

- **Remember device context:** retain the selected device when moving between Inbox/Outbox. Make the role clear: source device for incoming pushes, phone for SMS history, destination for outgoing note/link actions, and sending phone for SMS actions. Incompatible/unavailable device contexts must be explicit rather than silently changed. A view selector never reroutes a saved action.
- **Local SQLite for now:** place the SMS history database beside the explicitly defined application executable directory, not accidentally beside the system Python interpreter. Store history and identity associations, not credential values or derived keys.
- **Immediate cached reading:** device switching reads local records first. New messages are fetched and merged gradually in the core, within rate limits, without clearing rows, duplicating records, losing selection, or jumping the reading position. Show last successful sync separately from current refresh activity. Offline, failed sync, locked wallet, and decryption failures preserve already readable history.
- **Verified association:** partition by account, stable device identity, message identity, and direction. Never guess a source device from the current selection. Handle unidentified records and envelopes whose device becomes identifiable only after decryption. An account-wide feed may share a cursor rather than be fetched anew for each selected device.
- **Separate histories and actions:** historical SMS messages are history; a pending action is a command awaiting review. Reading, refreshing, device switching, and decrypting never submit, approve, resubmit, retry, or change a saved payload.
- **Encryption per device:** select a shared/default or device-specific KWallet reference for each relevant incoming device. Preserve `kdewallet → Pushbullet → api-key` and the existing `e2e-password`. The latter remains the shared debug/default entry for the current setup, not an assumption that every device always uses one key.
- **No secret leakage:** keys/passwords stay in KWallet; SQLite, screenshots, logs, and design artifacts contain no secret values or derived material. A missing/wrong key leaves existing readable cache intact.

## Device-management flow

The Devices main view keeps registration, capabilities, observed connectivity, and incoming decryption readiness separate. Active means registered, not online. The selected-device inspector shows the local key source and Manage encryption, plus Rename and Remove. Cache/sync information stays secondary.

**Add this Linux computer:** enter a friendly name, register the pbdesk client, retain the actual returned identity, then offer local encryption configuration. This registration does not create an Android phone or SMS capability.

**Phone or browser:** guide the user to set up the Pushbullet client on that device, sign in to the same account, grant Android SMS/notification permissions as appropriate, then refresh the device list. The client registers itself. “Open client downloads” is a proposed pbdesk control, not a claim about an authenticated website button.

**Local E2E:** choose the existing shared default or a device-specific entry in the same KWallet folder, optionally create a local entry, test local decryption, and show readiness. Use the password configured in the source client. This changes pbdesk's local key reference; it does not remotely configure the phone's encryption or upload a password to Pushbullet.

**Remove:** identify the exact device and confirm removal of its Pushbullet registration. Keep local SMS history by default, preserve the shared KWallet default, and never silently retarget saved actions. A removed destination requires an explicit eligibility/error state for affected actions, not a substitute device. Device removal and deletion of local history or credentials are separate choices.

The companion board uses fictional examples and illustrative readiness. It performs no registration, deletion, key test, or credential write.

## Message and lifecycle controls retained

- Inbox remains searchable and readable. A local-decryption badge belongs only on an incoming item actually decrypted locally, not ordinary note pushes or carrier SMS.
- Pending approval, approved/queued, submitting, upstream accepted, failed, uncertain, expired, and cancelled remain distinct. Pushbullet acceptance is not delivery confirmation. Uncertain outcomes get no blind retry.
- Compose selects an explicit destination; SMS uses a typed E.164 phone number. Saving creates a pending action and sends nothing. Notification titles never supply a recipient.
- Exact-payload review stays separate. Its transport acknowledgement begins unchecked, approval requires consent and readiness, and editing invalidates earlier consent. Actual target/body stay visible.
- Current outgoing note/link/SMS transport disclosures remain truthful: HTTPS to Pushbullet is provider-readable and is not end-to-end encryption; carrier SMS has its own limits.
- Web remains an opt-in loopback status companion in these references. No browser login, inbox, or send form is invented.
- Window close hides to tray when available; Exit desktop closes that client and keeps services running; Stop all stops the shared core/web host and disconnects clients.

## Technical research and source boundaries

These are implementation research items, not product exclusions or warning banners. Full SMS history and incremental sync remain in scope; the backend must verify the history protocol used by Pushbullet clients, decryption, merging, cursoring, and recovery of missed messages. The [public API documentation](https://docs.pushbullet.com/) describes push history, ephemeral streams, and SMS submission but not a complete phone-conversation-history endpoint. Mirrored notifications alone are not durable full SMS history; short-lived Text objects are not a full phone Sent folder. Research a verified ingestion path and report actual runtime outcomes once implemented.

Root verified the [public client downloads](https://www.pushbullet.com/apps), [official SMS setup](https://help.pushbullet.com/articles/how-do-i-send-text-messages-from-my-computer/), and [notification setup](https://help.pushbullet.com/articles/how-do-i-see-my-phones-notifications-on-my-computer/). These support client installation/account/permissions followed by discovery. The authenticated website was signed out; its current exact labels/click sequence were not inspected. The proposed dialogs are pbdesk's own design, not a reproduction of that website.

The [device API](https://docs.pushbullet.com/#create-device) supports custom-client registration and removal of a registration. The [official E2E explanation](https://blog.pushbullet.com/2015/08/11/end-to-end-encryption/) describes password entry and key derivation locally on each client, without server key provisioning. Multiple local references do not change the need for matching credentials on communicating clients.

## Finalization

Mr Orilla can finalize palette balance, density, text scale, and icon treatment. The accepted navigation, device context, full-history product target, local-first behavior, per-device local credentials, and separate outgoing approval carry forward. Exact frame metrics, labels, sample timestamps, IDs, and generated text are illustrative and require normal implementation design work.

No application implementation has started in this visual pass.


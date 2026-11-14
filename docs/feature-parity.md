# Interface parity — read, devices, and approved sending

Capabilities are implemented in the core before client exposure. Feature support and current send readiness are separate: disconnected or locked credentials remain visible in native status.

| Workflow | Core | Native desktop | Local web host | MCP stdio |
| --- | --- | --- | --- | --- |
| Stored pushes and mirrored notifications | Normalize, persist, deduplicate by source identity | Notifications | Deferred | Search/get |
| Encrypted mirrored events | Documented decryption with local E2E password; preserve ciphertext for retry | Encryption state and pending count; local credential reload | Status only | Capability/encryption state |
| Notification browsing/search/details | Versioned retained-history API | Native slate/jade list/detail, Ctrl+F, escaped text; Notifications → SMS → Devices | Deferred | `search_notifications`, `get_item` |
| Devices | Cached inventory, registration/rename/removal, explicit uncertain-registration recovery | Master/detail, Add Linux or phone/browser setup guide, exact removal, rename, refresh | Deferred | `list_devices` (cached, no refresh mutation) |
| SMS conversations | Account/device/thread/message/direction partitioned history; paced permanent-object synchronization | Independent phone context, threads beside incoming/outgoing chat bubbles, composer below, cached first, draft/selection/scroll retention | No message access | Not exposed |
| Per-device incoming decryption | Shared or per-device KWallet reference; authenticated sample test; no secret material in SQLite | Masked local entry creation, saved reference selection/test, readiness distinct from verified | Not exposed | Not exposed |
| Connectivity/capabilities | Signed discovery, readiness, scoped capabilities | Provider/encryption/send readiness | Read-only availability/provider status | `capabilities` |
| Live updates | Authenticated sequenced SSE with resync | Two-second bounded snapshot polling | Deferred | Event tool deferred |
| Note/link/SMS composition | Validated explicit device/channel/recipient; persistent idempotent action | Notifications Note/Link Compose; inline SMS Review & send; save pending, expiry, exact preview | Deferred | `request_action` creates pending only |
| Sending approval | Exact revision/fingerprint and explicit plaintext acknowledgment | Initially unchecked consent; separate Approve and send | Deferred | No approval/direct-send tool or flag |
| Saved action states | Persistent pending/ready/submitting/accepted/failed/uncertain/expired/cancelled | Message activity and exact review; all kinds/states remain accessible; acceptance distinguished from delivery | Deferred | `action_status` |
| Editing/cancellation | Atomic revision checks; edit invalidates approval and preserves expiry | Pending/queued action edit/cancel; stale-state feedback | Deferred | Not exposed |
| Compose drafts | Persistent draft synchronization deferred | Open forms and per-account/phone/conversation SMS drafts preserve typed text during polling/switches; restart persistence deferred | Deferred | Deferred |
| KDE notifications | Optional single core adapter; persisted at-most-once claims | No independent emitter | No independent emitter | No independent emitter |
| Web hosting control | Persisted preference; child start/stop | Enable, status, bind/port, Open | Status only | Not exposed |
| AI/automation/global pause | Deferred | Deferred | Deferred | Deferred |
| General activity history | Deferred beyond retained messages and action audit | Deferred beyond retained messages and Message activity | Deferred | Deferred |
| Browser accounts/session/CSRF | Deferred | Local setup only | No private content or actions | Not applicable |
| Remote hosting/remote MCP | Disabled | Not offered | Loopback only | Local stdio only |
| Login autostart | Separate opt-in | Off by default | Not offered | Not offered |

Outgoing note/link/SMS uses explicit `provider_plaintext`: Pushbullet can process the content, and SMS carrier delivery is not end-to-end encrypted. The incoming E2E password does not silently enable an undocumented outgoing E2E mode. Native approval requires acknowledgment for the exact displayed action; MCP cannot grant that acknowledgment or approve its own request.

A notification preview is not an addressable SMS conversation. The native compose form never infers a telephone number from notification text; SMS requires an explicit E.164 recipient and a selected SMS-capable device. Active device registration is shown without claiming current online connectivity. Pushbullet acceptance is not confirmation of delivery, and uncertain sends are not automatically retried.

All clients use the same core state and outbox pipeline. They never open SQLite, call Pushbullet, read provider secrets, decrypt provider events, or create provider listeners. The browser status host remains a deliberate narrow exception to workflow parity until private browser sessions and action security are implemented. Enabling it does not authorize remote exposure.

The Notifications/SMS revision passed independent final verification: **150 tests including Firefox**, **149 tests with Node absent** (one browser-marker test deselected), **23 authenticated worker/core HTTP checks against a fake provider**, and **13 independently inspected Qt captures on private Xvfb/xcb**. Its wheel preserves all 55 source/resource files byte-for-byte and extracted core imports/migrations pass without Qt. See the [final verification record](notifications-sms-verification.md) for exact wrappers, hashes and limits. The existing desktop, live core, root application database and KWallet defaults were left untouched; relaunch the existing **pbdesk: Desktop** profile to load the frontend, with no core restart or installation.

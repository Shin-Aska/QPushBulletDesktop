# User requirements for the next design revision

These decisions were supplied during visual review. They describe intended behavior; this document and the proposed images do not implement or migrate anything.

## Navigation and device context

- Keep the three main destinations in this order: **Inbox, Outbox, Devices**.
- Provide a device selector inside Inbox and Outbox, with access to both **Pushbullet pushes/notifications** and **SMS**. Remember the selected device when moving between those pages.
- Make the message's relationship to a device clear: incoming/source device, push destination, or SMS sending phone. Changing a view's device must never change the destination of a saved action.
- Devices is the place to inspect registered devices and their capabilities, add/register devices, remove devices, and configure the local E2E credential used for a device.
- The user wants each device's inbox and outbox. Do not represent a partial local capture as that device's complete history. Do not guess the device when a provider record omits identifying metadata.
- The target SMS Outbox includes the selected phone's sent-message history alongside clearly distinguished pending pbdesk actions. Keep historical messages separate from actions awaiting review; an action ledger alone is not the phone's full Sent history.

## Local SQLite and background synchronization

- Keep the local SMS SQLite database in the **same directory as the application executable for now**. The launch/package layout must define the application directory explicitly; it must not accidentally mean the system Python executable's directory.
- Show the selected device's locally stored records first. Device switching must not wait for a network request.
- Then let the core query for new messages in the background, gradually and within provider rate limits. Keep cached rows readable while refreshing, offline, or after a failed update.
- Show last successful synchronization and a quiet refresh indicator. Merge new records without losing the current reading position or duplicating messages.
- Partition local history by account, stable device identity, and message identity/direction. Maintain appropriate synchronization progress; an account-wide provider feed can share a cursor rather than being repeatedly fetched for every selected device.
- Reading, switching devices, refreshing, and decrypting must not submit or retry outgoing actions.

## Per-device end-to-end credentials

- Allow different E2E credentials for different devices. The user deliberately reused a password across the current devices for easier debugging; this must not become a permanent assumption.
- Keep credentials in the existing **kdewallet → Pushbullet** folder. Preserve `api-key` and the existing `e2e-password` entry as the shared/default reference for this setup. A device may refer to that default or a device-specific KWallet entry.
- SQLite stores message history and device associations, not credential values. Do not put passwords or derived key material in screenshots, logs, or design artifacts.
- A decryption or key lookup failure must preserve previously cached readable messages. An encrypted envelope may conceal its source device; implementation must account for that rather than assume a visible routing field exists before decryption.

## Device management

- **Add this Linux computer:** provide a friendly device name, register this pbdesk client with Pushbullet, then offer local encryption configuration. Use the actual returned device identity; adding a name must not pretend to create a functioning Android phone.
- **Connect a phone or browser:** explain setup of the Pushbullet client on that device with the same account, followed by refreshing the account's device list. For Android SMS and notification mirroring, explain the relevant permissions on the phone. This design pass does not install anything.
- **Remove:** name the exact selected device and confirm removal of its Pushbullet registration. Keep cached local SMS/history by default. Removing one device must not delete a shared KWallet credential, silently remove local history, or retarget saved outgoing actions.
- **E2E:** offer the shared default KWallet entry or a device-specific entry in the same wallet/folder, local decryption testing, and clear key readiness. A local credential choice must not be described as remotely changing the phone's encryption password. Passwords and key material are not sent to Pushbullet.
- Registered, encryption-ready, SMS-capable, and currently reachable are separate properties. Do not label a registered device online without an appropriate observation.

## Target product and implementation research

**Full SMS history and incremental synchronization are in scope.** The user explicitly rejected “not supported” or “captured SMS only” as the intended product experience. Selected mockups should show useful SMS conversations, cached data appearing first, and ordinary background synchronization. They illustrate the product being built, not the limitations of the current prototype. Keep API research and implementation gaps out of normal product copy.

Implementation must still verify how history is retrieved, merged, and decrypted. The [documented Pushbullet API](https://docs.pushbullet.com/) describes push history, a live ephemeral stream, and SMS submission, but does not document a complete phone SMS-conversation-history endpoint. This is a research task, not a decision to exclude history. Investigate the history protocol used by Pushbullet clients and build a verified ingestion path. Mirrored notifications alone are not full SMS history; short-lived submitted Text objects are not a durable Sent folder. Runtime sync/error states must reflect actual outcomes once implemented.

## Verified setup references

Checked on 2026-11-14. The public [Pushbullet Apps page](https://www.pushbullet.com/apps) offers clients for Android, Chrome, Firefox, and Windows. The [official SMS setup guide](https://help.pushbullet.com/articles/how-do-i-send-text-messages-from-my-computer/) describes installing the Android client, signing in, and granting SMS permission when requested. The [notification setup guide](https://help.pushbullet.com/articles/how-do-i-see-my-phones-notifications-on-my-computer/) describes enabling notification access. These support the client-setup-and-discovery flow for physical devices.

The [device API](https://docs.pushbullet.com/#create-device) supports registering a custom client and removing a device registration. The [official E2E explanation](https://blog.pushbullet.com/2015/08/11/end-to-end-encryption/) describes entering the encryption password locally in each client and deriving keys without server involvement. Its standard interoperable setup uses a matching password on communicating clients; pbdesk's local mapping can retain multiple key references without claiming remote key provisioning.

The browser was signed out, so the exact current authenticated website button labels and click sequence were not verified. The proposed pbdesk dialogs are our UI, based on the verified setup behavior above.

The desktop layout is feasible with the existing PyQt6/Qt 6 Widgets dependency. No package installation or switch to a different UI toolkit is required for this visual direction.

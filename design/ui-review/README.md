# pbdesk visual review

Image-only first-pass redesign requested for user finalization. No application source or live services were changed.

Open [the visual gallery](index.html) to compare the current application and Sol’s proposed images.

Your latest device management, full SMS history, local SQLite, background synchronization, and per-device KWallet requirements are recorded in [the design requirements](accepted-requirements.md). These describe the target product, not features already added by this visual pass.

## Sol’s concepts

- [Inbox](proposed/01-inbox-slate-jade-v5.png)
- [Devices](proposed/02-devices-slate-jade-v2.png)
- [Outbox](proposed/03-outbox-slate-jade-v2.png)
- [Compose and edit](proposed/04-compose-and-edit-slate-jade-v2.png)
- [Review and uncertain outcomes](proposed/05-review-and-outcome-slate-jade.png)
- [Settings and service controls](proposed/06-settings-and-service-controls-slate-jade.png)
- [Web status companion](proposed/07-web-status-responsive-slate-jade.png)
- [Empty and connection states](proposed/08-empty-and-connection-states-slate-jade-v2.png)
- [Add, remove, and encryption flows](proposed/09-device-management-flows.png)

The creative direction and exact prompt set are recorded in [Sol’s notes](sol-direction.md) and [prompts.json](prompts.json). Built-in image generation was used; no CLI/API fallback or dependency installation was used.

## Current captures

All 18 captures use fictional fixtures. Native windows were captured from the actual Qt Widgets on Wayland; web views use the existing Firefox installation with external requests blocked. The capture worker never contacts the live core or reads credentials.

- [Inbox and selected notification](current/01-inbox.png)
- [Device list](current/02-devices.png)
- [Outbox and action details](current/03-outbox.png)
- [Compose Note](current/04-compose-note.png)
- [Compose Link](current/05-compose-link.png)
- [Compose Sms](current/06-compose-sms.png)
- [Review and approve an exact message](current/07-review-approval.png)
- [Edit an action](current/08-edit-action.png)
- [Web hosting settings — disabled](current/09-web-settings-disabled.png)
- [Web hosting settings — enabled](current/10-web-settings-enabled.png)
- [Stop all services confirmation](current/11-stop-all-confirmation.png)
- [Application menu](current/12-application-menu.png)
- [Tray menu](current/13-tray-menu.png)
- [Empty inbox](current/14-inbox-empty.png)
- [Connection and locked-encryption state](current/15-connection-error.png)
- [Review an uncertain send](current/16-review-uncertain.png)
- [Web status — desktop](current/17-web-status-desktop.png)
- [Web status — mobile](current/18-web-status-mobile.png)

The current screenshots are evidence of existing UI. The proposed images are design mockups, not screenshots of implemented changes. Next is the user’s feedback and visual finalization before any UI implementation.

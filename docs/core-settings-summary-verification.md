# Core Settings and Summary-first verification

Independent final verification passed on the frozen implementation. The [exact report](../.runtime/core-settings-summary/final-verification.md), [machine-readable results](../.runtime/core-settings-summary/final-verification.json) and [isolated evidence archive](../.runtime/core-settings-summary/final-evidence.zip) retain commands, environment, logs, checks and source hashes. The [current native gallery](../design/ui-review/core-settings-summary/index.html) contains **32 inspected captures**: 23 synthetic native-widget views and nine actual production Qt/authenticated CoreAPI views. Earlier verification records and galleries remain historical evidence.

| Independent final check | Result |
| --- | --- |
| Full repository suite | 375 passed in 115.798s |
| Quiet focused suite | 263 passed in 79.509s |
| Lifecycle/process checks | 26 passed in 33.613s |
| Genuine production Qt/Core HTTP integration | 1 passed in 3.580s |

All final suites had zero failures, errors or skips. These scopes overlap and must not be summed into a unique-test total. Earlier component counts and draft captures do not replace these final independent results.

## Verified behavior

The sidebar is visually ordered **Summary → Notifications → SMS**. Logical page IDs remain `notifications`, `sms` and `summary`; the saved last main page, invalid-preference fallback, theme and unrelated preferences keep their prior meaning. Moving Summary to the top does not reset startup selection.

**Settings → Core** reports the authenticated backend instance, uptime, coupled/standalone mode, active desktop/web-server counts, core/web addresses, application/state/database paths and lease timings. Mode, addresses, paths and timings are launch-controlled read-only information. This tab does not relocate data or edit the backend launch profile.

**Deliver desktop notifications** is a requested preference persisted through the core store and applied live with **Apply notification setting**. Requested state, adapter availability and effective delivery state are distinct. A saved request cannot make an unavailable adapter work or override desktop Do Not Disturb. Existing at-most-once notification claims remain intact; submission does not guarantee a visible popup.

**Restart core…** is distinct from Stop All. The request binds the expected current instance and a request ID, accepts only quiescent operation, and waits for a different signed instance before reporting success. Ready/submitting actions or in-flight provider mutations prevent restart. Cancellation does not release asynchronous mutation ownership before the underlying operation finishes. Failed settings/restart/action requests are not automatically replayed.

A private, instance/generation-bound handoff with a maximum 60-second lifetime preserves the old core's exact demo/fixture/standalone mode, original notification launch flag and lease timings; the existing stored notification override remains effective. The helper holds no client lease. Desktop views and unsent drafts remain open, and an enabled web server keeps its same PID/instance for adoption by the replacement core. Restart preserves the Stop All generation; Stop All separately advances it and invalidates old clients and pending restart helpers. Invalid/expired handoffs fail explicitly instead of silently changing launch defaults.

The existing coupled lifetime, automatic recent Summary, manual guarded SMS insertion/approval, media loading, account boundaries and Qt stylesheet ownership guard passed regression checks. Summary still covers rolling 24-hour cached text with its bounded private JSON cache. Saved named presets, dated SQLite summary history, coverage/token/latency/reuse metrics and Markdown rendering are proposals only; this change adds no such storage or renderer.

## Restart evidence and source identity

The production Qt/Core HTTP check retained the actual desktop UI and unsent draft across a new signed core UUID while preserving the enabled web server's exact PID, process start time and signed instance. The Stop All generation stayed unchanged during Restart core. No action was created. Process cases also covered exact fixture/standalone launch-profile preservation and the Stop All/restart-helper race. An earlier harness PID-lookup failure was a diagnostic-fixture issue and was corrected; it was not a product failure.

Ten before/after phase maps and current/gallery hashes matched across 115 frozen source/test/build files. Canonical manifest SHA256: `3b6f9b8fca559b6c8d76807ca2c1e7034cc46af7d0e44e27bafc135e3515a32b`. The [file-level map](../.runtime/core-settings-summary/verified-full-source-after.json) covers product source/tests and build configuration; separately authorized README/docs/editor work was excluded. Documentation-only updates do not change that verified product source. Summary AI and the native Qt ownership guard are unchanged from baseline; this revision adds no Markdown renderer, summary-history schema or database migration.

Qt-blocked core/API startup/settings/capabilities and migration checks also passed. A wheel built from temporarily copied source with installed tooling included the new modules, without download or installation. Its SHA256 is `81ddd21d692a238fdf140771b8a2ae1566cd86dd9b3a5b71e41b007adda74031`.

## Activation and limits

After isolated final verification, a separate authorized guarded external activation completed for the configured editor core. [Live activation status](../.runtime/core-settings-summary/live-activation.json) and [private preservation evidence](../.runtime/core-settings-summary/restart-20261114T150801.888813Z/evidence.json) record a new signed ready core with `core_settings` and `core_restart` support, coupled mode, provider connected and incoming key ready with zero pending encrypted records. All 40 item and 1,026 SMS IDs/content and prior action IDs/content were preserved; the retained legacy database and provider credential reference were unchanged.

The old-core activation used guarded Stop All/cold start and restored web hosting enabled without error. It is **not** evidence of retaining the same live web PID. Same-PID/start-time/signed-instance preservation was proved separately by the new Restart core flow in the isolated production Qt/Core tests above.

The live notification preference is requested/effective off, with the adapter available and outcome disabled. AI metadata reports a configured dedicated key and `gpt-6-luna`; activation made **zero OpenAI validation calls**. The provider resumed ordinary synchronization. These live checks are separate from the isolated fictional test runs and make no live model-quality or billing claim.

The desktop was not relaunched. Relaunch the existing **pbdesk: Desktop** profile once to load the new UI; no installation or additional core restart is needed in this activated checkout. A configured account/key may then trigger automatic Summary and API usage. Keep existing profile/state/database/KWallet paths. Other older backends still need one-time external guarded activation before the new Core controls are available.

The evidence distinguishes real local HTTP/process restart tests from mocked provider, KWallet, OpenAI and media responses. Live model/key permissions and billing were not tested. Native Fusion was exercised; KDE/Breeze and a real tray manager were unavailable. Notification delivery used a fake `notify-send`, so actual KDE delivery remains untested. Real foreground process behavior was tested; systemd-user manager execution was not, while transient-unit/KillMode policy had mocked coverage. Short test lease timings and rounded UI values do not change the production defaults. The isolated evidence archive excludes live activation/restart directories and real database backups. No live operation was performed by the documentation update; the separate authorized activation is recorded above.

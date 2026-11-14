# Automatic Summary and client-lifecycle verification

Independent final verification passed on the frozen implementation. The [exact report](../.runtime/auto-summary-lifecycle/final-verification.md), [machine-readable results](../.runtime/auto-summary-lifecycle/final-verification.json) and [isolated evidence archive](../.runtime/auto-summary-lifecycle/final-evidence.zip) retain commands, logs, results and hashes, and the [current fictional native UI gallery](../design/ui-review/auto-summary-lifecycle/index.html) contains **22 captures**: ten synthetic states and twelve captures using actual connected Qt/Core HTTP. Earlier [media/AI/Settings verification](media-ai-settings-verification.md) and galleries remain historical evidence.

| Independent final check | Result |
| --- | --- |
| Normal full repository suite | 325 passed in 75.496s |
| Quiet focused suite | 213 passed in 42.009s |
| Lifecycle/process/browser matrix | 25 passed in 38.934s |
| Genuine automatic Qt/Core HTTP integration | 2 passed in 3.612s |
| Qt-blocked core and copied-source package checks | Passed |

All final suites had zero failures, errors or skips. These are overlapping verification scopes, not an aggregate test total. The process matrix includes actual Qt exit/hide/crash and the installed Firefox check that closing browser tabs leaves the web server and core alive.

## Native lifetime correction

Initial quiet-suite SIGSEGV logs remain retained as pre-fix diagnostic evidence. Python cyclic GC could collect parentless cyclic QWidget wrappers during synchronous Qt stylesheet repolishing. The narrow ownership guard retains `QApplication.allWidgets()` through that pass and change callbacks, then releases those references. A historical negative control reproduces the native crash; the fixed control exits successfully with forced cyclic GC enabled and verifies later collection. No GC disabling, threshold change, test reordering or xfail hides the fault. The correction changes lifetime handling rather than appearance. The deterministic regression, original quiet focused order and normal full suite passed; current captures use the same frozen product source.

## Verified behavior

Summary is an automatic account-wide digest of available cached notifications and SMS across conversations in the rolling last 24 hours. It selects at most 100 records and 24,000 input characters; it is independent of the visible Notifications filter and SMS thread. A known account and configured AI key permit generation at application startup. Empty recent history makes no cloud request. The UI retains the last successful digest with source counts, covered window and generation time while work updates or fails.

The desktop polls local status every five seconds and debounces changes for two seconds. The core coalesces fingerprints, serializes cloud work, and keeps a minimum 60-second interval between automatic paid attempts. Bounded private cache persistence retains successful summaries and cadence/backoff state across restarts; rejected-key/configuration failures latch rather than retrying each poll. Explicit Refresh does not bypass the automatic cadence. This is recent cached coverage, not a complete phone-history claim.

AI Settings uses an editable dropdown loaded from the configured account's fixed OpenAI model-list endpoint, preserving saved custom model IDs even when absent from the list. The default `gpt-6-luna` uses reasoning `none`, low text verbosity and a 1,000-token automatic-digest output cap. Requests use `store: false`, no tools and no reused conversation. The dedicated `kdewallet → Pushbullet → ai-openai-api-key` remains separate from provider/encryption entries and is never copied to file/database storage. Model catalog membership does not guarantee Responses compatibility, and store suppression does not establish training policy or zero data retention.

SMS drafting remains manual exact preview → Generate → review → explicit Insert. Insertion only edits the composer and rejects stale account/conversation/draft/pending-send context. The existing exact Review & send/approval flow remains required. Neither automatic Summary nor generated draft insertion sends a message or creates an outgoing action.

Normal core lifetime is coupled to opaque desktop/web **server-process** leases, bound to signed instance identity and launch generation. Defaults are 5-second renewal, 45-second expiry, 3-second final-release grace and 20-second startup grace. Tray hiding retains the desktop lease; full Exit releases only that desktop. Another desktop or web server keeps the core alive. Browser tabs do not hold leases, so closing a browser does not stop the server; disabling/stopping the web server releases it.

Crash recovery uses authenticated discovery and bounded serialized startup. A surviving signed web server can be adopted without a duplicate listener. Stop All stops core/web and advances a persistent generation so old clients cannot revive them; a fresh launch can start normally. Explicit core `--standalone` opts out for debug/installed-service use. Direct `python -m pbdesk.web` manages a core by default; `--standalone` is the explicit debug exception. Repository standalone debug profiles carry that flag; desktop profiles remain coupled. MCP holds no lease and cannot keep the coupled core alive alone; headless MCP automation requires an explicit standalone core.

## Evidence identity

Ten before/after phase maps matched across 108 source/test/build-configuration files. Current files and gallery source/image hashes match the verified artifact. The canonical manifest SHA256 is `088350bfd6594cf45f820e55e10e74b88203110c8f6e906d29fd27ef7f76e12a`; the authoritative file map is [verified-full-source-after.json](../.runtime/auto-summary-lifecycle/verified-full-source-after.json). README/docs/editor configuration was excluded because this separately authorized documentation work ran concurrently. Product source/tests remained frozen.

Qt-blocked core/API startup/settings/capabilities and migration `0006` passed. The wheel was built from a temporary copied source tree with installed tooling and `--no-isolation`, without downloading or installing anything, and included the required modules. Wheel SHA256: `ce0024f5b46a9eb4f1587a72bdf84da2da0a7b2a462520187043cac534cab020`.

## Activation and limits

After isolated final verification, a separate authorized guarded editor-core restart completed. [Live activation status](../.runtime/auto-summary-lifecycle/live-activation.json) and [preservation evidence](../.runtime/auto-summary-lifecycle/restart-20261114T141321.802919Z/evidence.json) record a signed ready new core with client lifecycle support, provider connected, incoming key ready with zero pending encrypted records, schema `0006`, and web hosting restored to its prior enabled state without error. All 37 item and 1,026 SMS IDs/content and three upstream-accepted actions were preserved; the retained legacy database and provider credential reference were unchanged.

The AI configuration reported `gpt-6-luna` and a configured dedicated key through metadata only. Activation made **zero OpenAI validation requests** and did not validate live model quality or billing. The existing provider resumed ordinary synchronization; this live activation is separate from the isolated mocked verification runs.

The desktop was not relaunched. Relaunch the existing **pbdesk: Desktop** profile to load the new UI. No installation or additional core restart is needed in this activated checkout. Once the new desktop recognizes the configured account/key, automatic Summary may generate and incur usage. Other older installations still require a guarded core-and-desktop restart; preserve established profile, state/database paths and KWallet folder.

Isolated verification distinguishes real local HTTP/process behavior from mocked external OpenAI/provider/media/KWallet responses. Live API-key permissions, model availability, output quality and billing remain untested. Native Fusion was exercised; true KDE/Breeze and a real tray manager were unavailable. The tray-hide test used a real QSystemTrayIcon with availability injected. Process checks used foreground mode and shortened deterministic lease timings; systemd-user manager integration was not exercised. No installer or live operation was performed by the documentation update; the separately authorized guarded activation is recorded above.

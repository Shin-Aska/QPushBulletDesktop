# SMS draft tone and context verification

## Behavior

SMS AI Draft locally previews at most the 20 most recent cached messages in the selected conversation. The table shows checkbox, time, sender and wrapped plain-text message. Refresh preserves checked messages that remain in the candidate set; new messages are unchecked. The older-message indicator explains when more history exists. Draft is disabled with no checked messages.

When drafting, only checked message IDs are sent in the explicit generation prepare request. The core accepts only strict IDs from the latest 20 messages in that exact account, device and conversation. Stale, duplicate, invalid or out-of-scope selections are rejected; the core does not silently replace selections. Generation input contains the exact selected-only quoted text, bounded by existing limits. Omitted message bodies never enter cloud input. Candidate preview text can be clipped locally and is not used to broaden generation context.

The initial tone is **Natural**. **Draft again** opens an editable prompt with Natural, Friendly, Brief, Professional, Warm and custom text (up to 200 characters). The accepted choice is remembered for the conversation. Cancel sends no request. A failed or canceled attempt keeps the old draft; editor changes made during a request are preserved. **Use draft** inserts the editable reply into the SMS composer, closes the assistant and focuses the composer. The usual **Review & send** approval flow remains necessary; drafting does not send SMS.

The feature uses the existing authenticated prepare/generate endpoints. Summary behavior, SQLite schema `0007`, existing summary history, `gpt-6-luna`, and the KWallet key path are unchanged. No migration is required.

## Verification status

Final independent verification passed: 453 full-suite tests in 203.90 seconds, 189 focused tests in 98.41 seconds, and one actual native Qt/AI worker/Core HTTP integration test in 2.67 seconds, with zero failures, errors or skips. All 123 source, test and dependency hashes matched the verified snapshot. The [verifier report](../.runtime/sms-tone-context/verifier-report.json), [gallery manifest](../.runtime/sms-tone-context/verifier-gallery-manifest.json), [source hash record](../.runtime/sms-tone-context/final-source-sha256.json) and [16-capture UI gallery](../design/ui-review/sms-tone-context/index.html) retain the evidence. Astra accepted six representative final views.

The [sanitized live activation record](../.runtime/sms-tone-context/live-activation.json) confirms one guarded restart, a signed ready core on schema `0007` using the same database, and enabled web health verified. Preservation checks passed for 1,026 SMS rows, three actions, 52 original notification IDs, six summary generations, six summary entries and presets/state. One normal provider mirror update retained its identity. Legacy database and credential references, AI settings and prior JSON state remained unchanged; only four forward-sync timestamps advanced. No schema migration occurred. The activation snapshot reports provider connected, key ready and zero encrypted pending. Validation made zero OpenAI requests and zero provider sends.

The activation helper did not persist the previous web identity, lifecycle timings or epoch, so exact before/after comparisons for those fields are not claimed. Relaunch the desktop once to load the UI update; no installation or further core restart is needed. Prior [saved summaries/SMS verification](saved-summaries-sms-verification.md) and its gallery remain historical records for their earlier revision.

The API and payload contract is documented in [backend-contract.md](../.runtime/sms-tone-context/backend-contract.md). The change is limited to SMS draft context and tone; existing summary payload defaults and history remain intact.

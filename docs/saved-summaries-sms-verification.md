# Saved summaries and editable SMS replies verification

Independent final verification passed on the frozen implementation. The [exact report](../.runtime/saved-summaries-sms/final-verification.md) and [machine-readable evidence](../.runtime/saved-summaries-sms/final-verification.json) retain commands, logs, checks and source identity. The [native review gallery](../design/ui-review/saved-summaries-sms/index.html) contains **89 inspected captures**: 81 current native component states, seven genuine production Qt/authenticated Core HTTP views and one historical before image. All visual checks passed; the before image is historical comparison, not a current-state claim.

| Independent final scope | Result |
| --- | --- |
| Full repository suite, including installed Firefox | 416 passed in 124.847s |
| Focused suites | 304 passed in 98.335s |
| Coupled lifecycle/process checks | 27 passed in 41.801s |
| Genuine production Qt/Core HTTP integration | 2 passed in 6.465s |
| Qt-blocked core migration and copied-source wheel | Passed; migration head `0007` |

Final suites had zero failures, errors or skips. These scopes overlap and must not be summed into a unique-test total. Initial component checks and previous revision counts are not substituted for these final independent results.

## Verified behavior

Additive migration `0007` creates four summary tables and three indices in the existing core-owned application SQLite database. It does not create a second database, alter old tables or replace retained SMS/items/actions/ciphertext/key references. Summary outputs/cache/history are now authoritative in SQL. Valid old private JSON cache imports idempotently as legacy history, preserving the original file and leaving unknown metrics null rather than fabricated zeros. New private runtime JSON contains only attempt/backoff metadata.

Immutable generation identity binds account, canonical scope/config, resolved model, prompt version and input fingerprint. Matching configurations can reuse a generation while each preset revision retains its captured scope and independent dated history entry. Saved date and original generation date remain immutable; pin/reuse metadata is separate. Unchanged polls do not create history entries or repeatedly inflate reuse.

Each account has one active preset shared across clients. Defaults All activity and Notifications only use 24 hours. Presets support cached source/device choices, exact nonempty device/thread pairs for selected SMS, 1/6/24/72/168-hour windows and inherited/override models. Browsing presets/history/details and saving inactive/new presets cause no cloud request or activation. **Save & apply** of an active preset deliberately changes its next eligible automatic scope with a cloud-use disclosure; active selection itself is shared, without a separate active-snapshot table.

Only the active preset drives automatic work, preserving 100-record/24,000-character bounds, coalescing/single-flight and global 60-second automatic paid cadence. Empty matching history makes no cloud request. In-flight paid results retain their captured old preset/model provenance when activation/configuration changes. Default Luna reasoning none/low verbosity, store:false/no tools, dedicated KWallet key and manual send approval remain unchanged.

Latest uses safe native Markdown with custom image/resource/HTML blocking and stable selection/scroll. History supports literal search, archived-preset filters, local calendar dates, pin filters and pagination. Details exposes captured scope/revision, saved/generated dates, coverage, truncation, model/prompt version, actual nullable input/output/cached-input tokens, monotonic latency and reuse metadata. Cost is unavailable; no prices are invented. Retention defaults to 30 days with 7/30/90/365 choices, affected-unpinned-count preview, revision-bound explicit confirmation and pin protection; generated rows are removed only without remaining references.

The compact SMS assistant has a recipient heading, collapsed local context and one large editable draft. Opening prepares local context only. Explicit Draft reply chains fresh prepare/generate; Use draft applies the current edited text with stale account/device/thread/composer/pending-send guards and confirmation before replacing a nonempty composer. Success closes/focuses the composer. Cancel/late-result/editor-change handling protects user edits. Read-only/group/history-only cases may offer copyable output while insertion is disabled with a truthful reason. No automatic send/action/approval occurs; normal Review & send remains the only outbound pipeline. Core restart/shutdown cancels and drains AI/owned SQL work before Store closes.

## Integrated evidence and source identity

Production Qt/AiWorker/Core HTTP exercised shared active-preset state across two desktops, real modal keyboard/filter flows, in-flight preset revisions, captured provenance, usage fields from mocked responses, legacy unknown metrics, pins/retention and a real Core restart without additional model generation. The SMS wire check generated into the genuine editable dialog, typed revised text, then verified Use draft closed it and focused the SMS composer. Stale/cancel cases preserved user text; zero actions or provider sends occurred. The active-preset cloud disclosure stays in the fixed footer above Save & apply at narrow sizes.

Ten before/after phase maps, current source and gallery hashes matched across 121 frozen source/test/build files. Canonical manifest SHA256: `ebae3aa3c4892432800c4d99fa94730e91bb92990c404a501b6e4488e1f4909f`; the [flat authoritative file map](../.runtime/saved-summaries-sms/final-full-source-after.json) excludes separately authorized documentation work. Product source/tests stayed frozen throughout final verification. Qt-blocked startup/migration reached `0007`, and a wheel built from temporarily copied source with installed tooling included the required modules without download or installation. Wheel SHA256: `84d3e37afafcf65e88c560d8a50123339e8f43072f9f149bed187757bad50554`.

## Representative native views

- [Editable SMS reply and Use draft](../design/ui-review/saved-summaries-sms/dark-normal-sms-editable.png)
- [Saved summary History](../design/ui-review/saved-summaries-sms/dark-normal-history.png)
- [Active-preset editor with persistent Save & apply disclosure](../design/ui-review/saved-summaries-sms/light-narrow-active-preset-editor.png)

## Activation and limits

After final independent PASS, the already-authorized guarded Core restart activated the configured editor core on schema `0007`. [Sanitized live activation status](../.runtime/saved-summaries-sms/live-activation.json) records signed readiness, the same existing root database path, unchanged signed web instance and Stop All generation, coupled lifecycle profile/timings, and preserved notification preference. The desktop was not relaunched.

Private before/after backup checks preserved all original 50 items, 1,026 SMS and three actions; locked/SMS envelope ciphertext, device-encryption references, old table/index definitions, retained legacy database, provider credential reference, AI settings and old JSON files also passed preservation checks. Sensitive backups and live restart directories remain separate from the isolated fictional evidence package.

Provider status is connected, incoming encryption is key ready with zero pending encrypted records, web hosting is enabled, and desktop notifications remain requested/effective on with the adapter available and outcome ready. AI metadata reports a configured dedicated key and `gpt-6-luna`. Activation validation made **zero OpenAI requests and zero provider sends**; ordinary user-authorized background automatic work was unchanged. These live checks are separate from mocked test evidence and do not validate cloud response quality or billing.

Relaunch the existing **pbdesk: Desktop** profile once for the new UI. No installation or additional core restart is needed in this activated checkout. A configured active preset may automatically generate and incur API usage. Other older installations still need guarded `0006`→`0007` activation; preserve the established database/profile/state/KWallet paths.

Isolated verification used actual Qt/Core HTTP and existing-SQLite migration checks with external OpenAI/provider/media/KWallet responses absent or mocked. No live model-quality/billing or real-send claim follows from fictional tests. Earlier component counts and prior verification records remain historical rather than final results for this integration. Documentation work itself performed no live operation; the separately executed guarded activation is recorded above.

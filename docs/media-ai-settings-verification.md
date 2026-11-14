# Media, AI and Settings verification

Independent final verification passed on 14 November 2026 with no actionable source or visual findings remaining. The [exact report](../.runtime/media-ai-settings/final-verification.md) and [machine-readable evidence](../.runtime/media-ai-settings/final-verification.json) retain commands, environment overrides, logs, JUnit results and hashes. The [fictional UI gallery](../design/ui-review/media-ai-settings/index.html) contains all 51 inspected production-widget captures.

| Check | Result |
| --- | --- |
| Full repository suite | 289 passed in 39.20s |
| Focused suites | 209 passed in 32.46s |
| Additional real Qt/Core API loopback HTTP integration | 3 passed in 5.52s |
| Installed Firefox integration | Passed in 2.672s, external requests blocked |
| Native captures | 51 inspected; independent design review approved 15 representative images and the Summary correction |

All test phases had zero failures, errors or skips. The additional integration used genuine production workers/widgets and signed/authenticated HTTP against an isolated Core API. External OpenAI/media transports, KWallet and the provider were mocked. No package was installed or downloaded, no real OpenAI call or charge occurred, and no running user core, desktop, wallet, account, device or user database was changed.

## Verified behavior

The main rail contains Notifications, SMS and Summary. Reusable Settings contains Appearance, Devices, AI and Local Web; existing registration, rename, removal, encryption, history, filtering and keyboard controls remain accessible. Atomic local preferences restore the last main page and preserve color scheme and unknown fields. Settings never becomes the stored startup page.

Native notification icons/application names, inline contact thumbnails, explicit remote-contact/MMS/file-image loading, larger previews and expired-image Retry were exercised. The core resolves retained account-scoped references, restricts HTTPS origins, rejects redirects and bounds raster bytes/dimensions. Image-host requests carry no provider authorization, access-token or cookie headers. Account/reference changes reject stale output; previews use bounded memory caching rather than disk caching.

AI tests verified local exact-source preview before explicit Generate, bounded selected text with media excluded, the fixed OpenAI Responses host, the dedicated AI key and `store: false` without tools or conversation reuse. Startup, polling, navigation and reading AI settings cause no OpenAI request. Completed summaries retain their captured source/count/time through real polling, notification changes and unrelated SMS draft edits without another upload. Account/source/conversation changes invalidate applicable context; changed prepared content is rejected before another key read or cloud request.

Generated reply insertion changes only the editable SMS composer. A changed conversation, draft, account or pending-send state rejects stale insertion. The ordinary exact review and plaintext acknowledgment remain required to send; integration created zero actions and made zero provider-send calls. Prior full supplied notification text/safe links, quiet status, accepted-SMS Activity retention and exact-revision/fingerprint Review closing also passed.

Review corrections include retained completed summaries, local ownership of media errors, compact partial-sync metadata without horizontal overflow, and native action minimum heights. At 850×540 with three conversations, MMS, wrapped contact metadata and a real refresh-error banner, horizontal overflow was zero; AI and Review buttons were 31px, matching native size hints, and the composer remained 86px. Native vertical scrolling deliberately keeps controls reachable.

## Migration, packaging and evidence identity

Additive migration `0006` preserves existing IDs, text, ciphertext, settings, key references and account partitions while adding media metadata. Versioned SMS normalization causes an ordinary paced reread of unchanged threads. A Qt-blocked core/API startup, settings/capabilities and migration check passed. A wheel built from temporarily copied source with installed tooling and `--no-isolation` included all eight new API/service/desktop/preference/migration modules; it was not installed. Wheel SHA256: `43b618d6dd1c7f86e1ac2ea0e240108bd1c42722b5f5bbd340a8165cdb100122`.

Eight final before/after hash maps matched across 119 frozen source/test/docs/README files. The canonical manifest SHA256 is `4faf83b50de375f43f0851f00f5342d9967f41b82372273fe7dc407ccf4e2047`; the map is [final2-full-source-after.json](../.runtime/media-ai-settings/final2-full-source-after.json). Application source and tests were frozen and verified before this intentional documentation-only update, so the canonical manifest predates this record and accompanying README/protocol/parity edits. Earlier failed preflight/first-freeze evidence remains archived, and prior verification records remain historical evidence.

## Activation and limits

This revision changes backend routes and the SQLite schema. **Restart both the core and desktop**: choose **Application → Stop all services…** in the existing application, close the old desktop, then launch the existing **pbdesk: Desktop** editor profile. Other desktop/MCP clients disconnect when services stop. Keep the same profile, data directories and KWallet folder; no installation is needed in the configured checkout. Verification did not perform this live restart.

Actual AI use still requires a user-configured OpenAI API key at `kdewallet → Pushbullet → ai-openai-api-key`, separate from provider/encryption entries. Live model quality, account access and billing were not tested. `store: false` does not establish training policy or zero data retention. The runtime offered native Windows/Fusion styles and used Fusion; true KDE/Breeze integration remains untested. Source-phone omissions, expired media and previously discarded mirror icons cannot be reconstructed merely by changing the presentation. Available SMS snapshots are not a complete all-time phone-backup guarantee.

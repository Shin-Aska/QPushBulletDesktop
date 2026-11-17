# Packaged release acceptance

The owner reported “It works” and “Looks good” for the delivered AppImage and
explicitly authorized the first stable 1.0.0 GitHub publication. This records basic
native product acceptance and publication authorization. It does not claim an
independent real-KWallet, tray, notifications, or KDE/Wayland integration test.
Those optional integration checks remain identified in the verification output.

## Immutable artifact sequence

1. Build the image and matching source/license inventory. Record image SHA256,
   source revision, version, Python runtime identity, AppImage runtime SHA256,
   lockfile hashes, and the source-material file hashes.
2. Run `packaging/verify_release.py` against those immutable bytes and a pinned
   older actual binary. Keep the sanitized JSON and its SHA256 as historical
   evidence. The verifier never modifies either input image.
3. A separate finalizer validates the exact image/source/material identities and
   every required outcome, then updates external build-info and SHA256SUMS. It
   does not rebuild or modify the tested image. Mutable build-info is deliberately
   excluded from the evidence hash dependencies.

The verifier reports measured technical acceptance. Corresponding-source legal
readiness is handled separately; this document makes no legal readiness claim.

## Baseline selection

The first 1.0.0 upgrade baseline is the already built and verified 0.1.0 AppImage:

- Source: `15c65759ce73b9dd59448b2a46a6a1fa0ab702ca`.
- SHA256: `673c2b2d9dc08db2a4e720cba8d43be62b35dbd2e954e931dda6e0c37e112643`.
- Original Actions run: `43913951825`; artifact: `14249652332`.

That Actions artifact has short retention. Its run identifier is provenance, not
a permanent download dependency. Retain the accepted sanitized proof and hashes.
Later releases select a published, non-prerelease stable version strictly lower
than the target and pin its tag, source revision, asset name, and SHA256. The
workflow supplies that exact baseline file to the verifier. Rebuilding old source
does not establish identity with the originally delivered baseline binary.

An explicit same-version replacement fixture trial uses `classification=trial`
and `relation=same`. It validates the harness and replacement mechanics and can
never satisfy the encrypted-profile upgrade release gate.

## Running the verifier

Use the existing test environment and installed private-session tools:

```sh
.test-venv/bin/python packaging/verify_release.py \
  --old-image /absolute/path/old/pbdesk-0.1.0-x86_64.AppImage \
  --new-image /absolute/path/new/pbdesk-1.0.0-x86_64.AppImage \
  --classification release \
  --baseline-sha256 673c2b2d9dc08db2a4e720cba8d43be62b35dbd2e954e931dda6e0c37e112643 \
  --baseline-source-revision 15c65759ce73b9dd59448b2a46a6a1fa0ab702ca \
  --baseline-version 0.1.0 \
  --smoke-evidence /absolute/path/new/smoke-evidence.json \
  --output /absolute/path/release-acceptance.json
```

The output must be a new file. Existing binary, metadata, source/license material,
smoke evidence, and prior acceptance files cannot be overwritten. Exclusive file
creation also prevents a path collision introduced after the initial check.

The script creates its own private temporary profiles, session D-Bus, Xvfb,
in-memory fake KWallet, loopback ports, and unrelated application working
directory. Frozen processes receive an allowlisted environment without source
`PYTHONPATH`, inherited profiles, provider keys, or the user's session bus. Cleanup
signals only fixture descendants. No installed production profile is opened or
activated. All messages, phone numbers, references, summaries, and keys are
fictional. Demo mode prevents provider or paid-AI operations; no send is attempted.

## Required observations

The report exports nineteen exact required case IDs through `REQUIRED_CHECKS`.
`validate_acceptance` requires the independently selected baseline SHA/source/
version policy from its caller. It fails closed for trial reports, same-version replacement,
downgrades, a different target SHA/source, incomplete material bindings, duplicate
or missing outcomes, and failed or unmeasured cases. It checks concrete reported
measurements as well as status labels. The actual owned frozen process's cached
runtime identity, packaged diagnostics version, and outer AppImage runtime byte
hash must agree with the target metadata. Source revision is bound by the
independent CI/source archive inventory; it is not invented as embedded Git data.

The upgrade fixture protects history through the old actual binary, exports a
private recovery backup, and stops its owned core normally. Offline fixture
seeding uses the ordinary HistorySecurity and private fake-wallet adapter. The
old binary must then read the notification, SMS, preferences, saved summary, and
provider credential-location reference through its normal local API. Replacing
the same executable path with the target bytes must preserve those values, the
saved-summary identity, service credentials, original wallet reference, and
original database key. No new history enrollment is accepted during upgrade.

The recovery fixture stops the owned core and erases its single fictional key by
restarting only that profile's in-memory wallet fixture. The application must
remain locked and reject history reads, malformed recovery, and a checksum-valid
backup with the wrong key. The valid backup must preserve the database identity
and key, create a fresh wallet entry, retain the data, and unlock normally after
another actual-binary restart.

A separate legacy plaintext fixture requires explicit migration. An inotify
observer detects the real durable `exporting` journal boundary and sends SIGSTOP,
then SIGKILL, only to the owned frozen core. There is no production pause hook,
injected module, or raw-key environment bypass. Restart must offer explicit
resume, reuse the enrolled wallet reference, retain the legacy notification and
preferences, and complete encrypted activation. Failing to observe the boundary
is a failed acceptance case, never an assumed pass.

After upgrade, recovery, and migration, ordinary synthetic preference writes
populate the WAL and restore the original preferences. Both database and WAL
bytes must omit the known fictional plaintext. The database must reject an
independent standard-SQLite read.

## Evidence and scope

The JSON binds old/new image SHA256 and source revisions, versions, runtime
identities, immutable source-material digests, and per-case outcomes. It contains
no recovery material, wallet values, bearer credentials, raw child logs, private
profiles, or database files. Failure diagnostics contain exception types and
frame locations without exception values or locals.

The existing actual-AppImage smoke report is reused only when its SHA matches the
target and all 46 generic packaged checks pass. That report covers process
survival, core restart, and Stop All; the release verifier does not repeat those
cases as new coverage. The separate frozen SMS fixture supplies sixteen real-core
checks, light/dark screenshots, and seven rendered long-thread checks on the
exact target artifact when requested.

No local installation or broad source-suite rerun is required merely to execute
this harness. Focused helper tests cover evidence binding, ownership boundaries,
fixture integrity, recovery input construction, and environment isolation.

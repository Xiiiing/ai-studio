# Work package 10 validation evidence

Branch: `feat/versioned-creative-prompts`. Fetched main baseline:
`4aaece74ab54f38c125c564accc20f4134fb5bab`.

## Original implementation validation (retained historical evidence)

| Validation | Actual result |
| --- | --- |
| `python -m pytest -q tests/test_factory_*.py` | **548 passed**, 94.83 seconds, original implementation. No skipped/deleted/weakened tests. |
| `npm ci --ignore-scripts` | Successful, 18 packages installed from the unchanged lockfile. |
| `node --test tests/test_factory*.mjs` | **54 passed**, 0 failed, 0 skipped; Node 22.16.0. |
| `python scripts/browser_fixtures.py` | CPU FFmpeg test-pattern MP4 and PNG generated inside the isolated worktree. |
| `npm run test:browser` | **5 passed**, 13.6 seconds; real Chromium with existing mocked API fixtures. |
| `git diff --check` | Passed. |
| Business diff review | Existing changes limited to creative prompt loading, explicit renderer provenance, additive call/cache receipt metadata. |
| File and known-secret-pattern review | No runtime/production database, private config, environment file, key, model weight or lockfile changes included. Pattern checks do not claim exhaustive secret detection. |

Python: CPython 3.12.13 from the existing factory environment, used without
installing into or changing that environment. `PYTHONDONTWRITEBYTECODE=1` and
read-only pre-existing pypinyin/OpenCC audit packages were used for the full suite.
No test uses a live production project. The browser run used the pre-existing image
`mcr.microsoft.com/playwright@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f`
with `--network none --cpus 2 --memory 4g --pids-limit 256`, the isolated checkout
and read-only Python tooling mounts; no GPU, production data, host network or
Docker socket was mounted. Container Node was 24.21.0.

Exact logs remain in the isolated checkout's ignored `runtime/audit-evidence/`:
`python-final.log`, `npm-ci.log`, `node-tests.log`, `browser-validation.log`,
`precommit-review.diff`. The committed test corpus is original synthetic text.
CPU fixture SHA-256 values:

- MP4: `c45a53d8566af65486a063366fdb0a98316ef99465a65614572b074fe2da4dc8`
- PNG: `93585e916ad2fb5f50d875ddb44f8b1b4fa55bf3693c049d336a1594a765d9c9`

## Test-first evidence

The 18 new prompt contracts initially failed because the registry was absent;
they passed after implementation. The next 10 offline-comparison cases initially
could not set up because the new experiment module did not exist. Adversarial
checks then exposed four defects in the newly written offline tool: premature
path normalization, holdout reuse after unrelated development-corpus changes,
missing director editable-field enforcement and absent per-dimension regression
statistics. These were fixed and the same tests passed; these are not claims of
exploits reproduced against the production service. Two additional malformed
catalog cases initially raised uncontrolled exceptions and now fail as explicit
validation errors. The original suite included 54 new test cases in five files.

Verified boundaries include exact v1 text and legacy cache bytes, role/context
isolation, derived profile signatures, succeeded/failed prompt receipts, unknown
historical versions, independent sampling identity, frozen model/config policy,
quote/checksum verification, complete-repeat aggregation, negative outcomes,
explicit holdout consumption and independent non-overwriting CLI reports.
Synthetic external receipts test consistency checks only; they do not prove a
provider actually generated any content.

## Publication repair validation, 2026-10-15

Seven new regression cases initially failed (0 passed), demonstrating four
bounded issues in the unpublished implementation: catalog schema references,
contradictory failed receipts for recorded results, write/read size mismatch,
and holdout exposure before the old compare-time consumption gate. Their trigger
conditions are controlled catalogs/evidence and local experiment operations, not
confirmed exploitation of a production HTTP service.

After fixes: **61 focused tests passed in 9.54s**; the full original command
`python -m pytest -q tests/test_factory_*.py` completed with **555 passed in
84.38s**, retaining one existing multiprocessing/fork deprecation warning.
`npm ci --ignore-scripts` succeeded; Node contracts: **54 passed**, no skips;
CPU fixture creation succeeded; Chromium browser regression: **5 passed in
13.9s** using the same isolated, network-disabled image and mocked APIs.

Fresh logs are in `runtime/release-evidence/`: `regressions-before.log`,
`focused.log`, `python-full.log`, `npm-ci.log`, `node.log`, `browser.log`.
All five original v1 prompt texts and the entire six-case corpus remain byte-equal
to the original local implementation. The existing holdout regression was
strengthened to require rejection before a second release, not merely when
computing its final score. No test was skipped, deleted or given a looser threshold.

The Runner disconnected after these completed tests, before documentation edits
were dispatched. The original worktree was preserved. Publication used the
existing authorized GitHub connection, and the repair commit was read back to
verify that its diff matched the tested edits. Repository publication/CI evidence
is in `NEXT_ROUND_PR_REPORT.md` and the actual formal PR, not inferred from local
files. New documentation does not change the tested code.

## Not run / no unsupported conclusions

Real writer, storyboard or judge model calls: **not_run**.
Real GPU inference and real cloud video tasks: **not_run**.
Artistic quality improvement: **not established**; the v1 texts are byte-equal to
the previous implementation, and no improvement percentage is reported.
Production deployment, database migration, live restore, service restart and
approved-media changes: **not performed**.

A local commit or passing mock suite alone is not reported as a published PR or
a successful remote CI run. Cross-PR 7–10 integration was not rerun in this task.

# Versioned creative prompts

Work package 10. Branch: `feat/versioned-creative-prompts`. Audit baseline:
`4aaece74ab54f38c125c564accc20f4134fb5bab` (`origin/main`, fetched before the isolated worktree was created).

## Problem and evidence

At the baseline, `factory/creative.py` embedded creator, independent critic,
screenwriter and visual designer instructions; `factory/storyboard.py` embedded
the director instructions. Model routing, a two-round independent review limit,
human-feedback precedence, profile adaptation and content-addressed step caching
already existed. They are not reimplemented here.

The cache signature already included the rendered system text, actual resolved
model identity, output schema, input context and sampling arguments. Replacing it
with a version-only cache key would regress provenance and invalidate compatible
history unnecessarily. The actual gaps were separately reviewable prompt
contracts, actual-call version attribution, and a repeatable offline comparison
protocol. Schema acceptance alone was not evidence of an artistic improvement.

## Modules and contracts

| Module / data | Responsibility |
| --- | --- |
| `factory/prompts/catalog.json` | Explicit default versions, role responsibilities, closed top-level input-context schemas, output-schema references and template checksums. Contains no model assignment. |
| `factory/prompts/<role>/1.0.0.txt` | The exact original instruction bytes for five principal roles. No third-party prompt text is copied. |
| `factory/prompt_registry.py` | Bound and validate the selected local catalog/template; immutable string-compatible prompt identity; explicit renderer derivations; private-text-free receipt metadata. |
| `factory/profile_creative.py` | Preserve existing profile rendering while carrying the original identity through the transformation. |
| `factory/schemas.py` | Keep the original signature and cache path; validate role/context before lookup; add metadata only to newly written cache records. |
| `factory/llm.py` | Record the actual template/version, rendered text hash, composed system-rule hash, input/schema hashes, unique call ID and canonical parsed-output digest in the existing receipt. |
| `factory/creative_experiment.py` | Freeze exact requests, variants, common model policy, corpus/split, repeats, judge and evaluator source under a trusted external seal. No execution. |
| `factory/creative_comparison.py` | Import receipts, bind anonymous outputs, validate quotes, aggregate every replicate, preserve regressions and prevent silent holdout reuse. |
| `factory/creative_evidence_io.py` | Bounded, duplicate-key/nonfinite-rejecting JSON; POSIX no-follow reads/writes; exclusive outputs. |
| `factory/creative_experiment_cli.py` | Offline commands and independent report export. No provider-running command is registered. |

The registered roles are `creator`, `independent_critic`, `screenwriter`,
`visual_designer` and `storyboard_director`. The input schemas intentionally
retain the existing nested business objects; they constrain which top-level
contexts a role may receive, not a second copy of every evolving project schema.
The referenced production output schemas remain authoritative and the actual
rendered schema checksum is captured on every call.

Specialized story-goal editor/critic and style-cleanup prompts are not relabelled
as one of these roles. Their specialized outer-cache and recovery policies stay
unchanged. Unregistered strings have `version: null`; a role name is never used
to invent a historical prompt version.

## Compatibility and invalidation

`PromptText` is still a `str`: string equality and JSON serialization are unchanged,
and deep copies retain its immutable metadata. The initial extraction is checked
against the five frozen SHA-256 values in `tests/fixtures/prompt-baseline-v1.json`.
The separate-screenwriter and production-profile transformations preserve exactly
the pre-existing rendered text and add explicit transformation identifiers.

The original cache key dictionary and filename format are unchanged. A byte-equal
v1 extraction reuses the old cache file without rewriting it or assigning a new
version to an old call. A changed rendered prompt, payload, model or output schema
invalidates the affected step using the existing signature. A metadata-only rename
with identical behavior does not trigger a new paid call merely to relabel history.
The new contract check still runs before any cache reuse.

No database migration is needed. No execution is enqueued, no cloud request is
submitted, and no production/series media path is rewritten. The two-round review
limit, independent critic, latest human-feedback priority, restricted director
fields and preservation of unselected shots remain the existing implementation.

The catalog is loaded with the application code, not hot-reloaded by a watcher.
Adding a version is a normal reviewed source change: add a new versioned file and
checksum/contract, explicitly select it in the catalog, run regression tests, then
run a separately authorized comparison. Do not edit v1 in place. A real behavior
change must keep its negative or neutral experimental result; there is no measured
quality improvement in this PR.

## Identity and context boundaries

Stable character identity belongs in the existing Series Bible/versioned assets.
Reusable wardrobe or injury variants remain distinct from identity. Per-shot
position, expression, held props and transient action remain shot facts. Narrative
causality and producer intent remain story context. This extraction does not add
plot objects to generic portrait/style prompts, silently advance approved series
references, or replace the existing asset-binding implementation.

## Upstream source study and adaptation

### Toonflow

Repository: <https://github.com/HBAI-Ltd/Toonflow-app>

Pinned commit: `d2e94aca7b59659ff6b4ca15f03cf134ec7f0e1d`.
`LICENSE` at this exact commit is MIT (not an assumed license of a moving branch).

Source read:

- `packages/teams/storyboardTeam/team.json`
- `packages/teams/storyboardTeam/members/director.md`
- `packages/teams/storyboardTeam/members/writer.md`
- `packages/teams/storyboardTeam/members/reviewer.md`
- `packages/teams/storyboardTeam/knowledge/continuity.md`

The team manifest separates member instructions, scoped tool/delegation rights and
shared knowledge. Writer and reviewer do not invent confirmed media or grant
production approval; the director owns delegation rather than unbounded recursive
agent creation. AI Studio adopts separately versioned responsibilities, explicit
context and bounded review authority within its existing LangGraph pipeline. It
does not import the TypeScript team runtime, a second orchestrator, its prompts,
or a frontend framework. No claim is made that Toonflow tests were executed here.

### Drama Skills

Repository: <https://github.com/zenstory-ai/drama-skills>

Pinned commit: `f53fb206507cb0cfb07fd29b692156dd4fb8fe7d`.
`LICENSE` at this commit is MIT.

Source read:

- `evaluations/README.md`
- `evaluations/content_quality_gate.py`: artifact verification, strict receipts,
  trusted seals, frozen corpus/config metadata and replicate/case aggregation.
- `tests/test_content_quality_gate.py`: missing/duplicate replicates, reused
  observations, relabelled cases, source changes, equal weighting and regressions
  hidden by a positive macro score.
- `evaluations/content-quality-rubric.md`
- `skills/short-drama-write/SKILL.md`
- `skills/short-drama-assets/references/identity-vs-variant.md`
- `skills/short-drama-storyboard/references/review-and-fixtures.md`

The upstream distinguishes structural validity from artistic judgment, freezes
inputs outside the run's own claims, binds receipts to actual artifacts and keeps
development separate from holdout. AI Studio implements these mechanisms locally,
using its existing six text-critic dimensions rather than importing another film
score. Both label positions and all predetermined replicates are retained. Public
contract fixtures are not represented as unseen holdout material.

Not adopted: upstream CLI-agent runners, private/reference-run source material,
its eight-axis weights or an implied statistical guarantee from a small fixture
set. The first implementation is entirely offline; external evidence remains an
operator attestation rather than proof that a supplier actually ran a model.
No upstream implementation or fixture text was copied, and no upstream tests were
claimed as local test results.

## Validation and deployment

Run the full Python, Node and browser commands required by the repository. Exact
results and the tested implementation commit are recorded in the PR and the round
reports; evidence logs are kept in the isolated worktree's ignored
`runtime/audit-evidence/` directory. The browser videos are CPU-generated test
patterns and the API responses are fixtures, not generated drama footage.

Deployment is a normal application-code update including the `factory/prompts/`
data files. Do not copy only the Python module without its catalog/templates.
No production service was restarted or configured during development. Roll back
by restoring the previous application code; historical caches and receipts remain
readable because consumers tolerate the new metadata fields. New offline run
protocols must be replayed with their frozen source revision; changed or missing
sources block verification rather than being silently accepted.

# Offline creative A/B protocol v1

This protocol compares screenplay text or scoped storyboard-direction outputs.
It is not the production acceptance gate and never approves media. The existing
`factory.evaluation` remains responsible for film evaluation. Six dimensions are
reused from `factory.schemas.SCORE_KEYS`, each on the existing 0..10 scale; schema
validity is a separate prerequisite, not an artistic score.

## Freeze, without running a model

Use the correct Python 3.12 environment in an isolated checkout. The example
config below deliberately has a disabled, placeholder model. Create the parent
output/seal directories explicitly; every actual run/report directory must be new.

```bash
mkdir -p runtime/creative-runs runtime/creative-seals
python -m factory.creative_experiment freeze \
  --corpus evaluation/creative/corpus-v1.json \
  --config evaluation/creative/model-config.example.json \
  --role screenwriter --baseline-version 1.0.0 --candidate-version 1.0.0 \
  --samples 3 --seed 731 --judge-id reviewer-1 \
  --output runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json
```

Equal v1 variants are an extraction/control experiment, not an optimized candidate.
Use `--role storyboard_director` for the other task. To compare a newly reviewed
version, supply `--candidate-root <catalog-directory>` and its explicit version.
The two variants share exact case inputs, model identity, config revision, output
schema, sampling arguments, seed policy, repeat count and rubric. A model judge
also requires an explicit `--judge-config <config.json>` at freeze time; otherwise
the designated reviewer is human. Model versions are taken from the supplied
configuration and actual receipts, never guessed from current defaults afterward.

`plan.json` contains the private arm mapping and provenance. Each opaque task ID
has one `requests/<id>.json` with exact system text, payload, output schema and
common model policy. These requests contain no arm/split/negative-control label or
rubric. `corpus.json` preserves the frozen semantic input; JSON object checksums
use canonical UTF-8 JSON, with sorted keys, no extra separators and no nonfinite
numbers. System/prompt text checksums are SHA-256 of exact UTF-8 bytes.

The external seal binds the plan, corpus and exact evaluator source files. Keep
it under operator control, outside the run. The seal is a consistency/trust root,
not a digital signature or an authentication system. Replacing both evidence and
the trusted seal can forge a result; this tool does not claim to prevent a
malicious operator from doing so.

## Explicit external observations only

No CLI command launches a model, provider discovery, a GPU workload, a cloud video
task or an agent. Requests can only be executed separately in an explicitly
authorized existing model-call environment. This PR did not do that. A missing,
failed or unverifiable call cannot be replaced with an assumed successful result.
Video charges and uncertain cloud tasks remain under the existing budget/approval
and task-recovery mechanisms; this tool neither invokes nor bypasses them.

For every predetermined task, import the original parsed output and original
AI Studio call receipt. New receipts record a unique call ID and canonical parsed
output checksum. The importer verifies the actual rendered/composed prompt,
input, schema, config/model identity, sampling policy and output digest. Reusing a
call ID as another replicate is rejected. Original unregistered prompt versions
stay unknown even when their exact content can be bound to the frozen request.

```bash
python -m factory.creative_experiment import-generation \
  runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json \
  --task <opaque-task-id> --result <parsed-output.json> --receipt <receipt.json> \
  --evidence-mode external
```

A receipt whose SDK status is `failed` can be imported without `--result`. This
retains the failed attempt and blocks a complete comparison; it is not permission
to repeat a potentially charged request. Resolve uncertain provider outcomes
through the original task/call workflow rather than submitting another experiment
request automatically. Importing the same task a second time is rejected; original
observations are never overwritten or selected away.

Synthetic fixtures use `--evidence-mode simulation` and a receipt explicitly
labelled `simulated`, with a matching output checksum. A completed simulation can
validate the protocol, but its report keeps `model_validation: not_run`,
`conclusion: not_established` and `production_approval: false`.

## Anonymous review

Only after all planned generation outputs are accounted for can a blind bundle
be exported. Place it outside the private run directory and give the judge only
this bundle, not the plan, source prompt catalog or previous score files.

```bash
python -m factory.creative_experiment blind \
  runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json \
  --output runtime/creative-runs/writer-blind-v1
```

Each replicate appears in both A/B positions. Opaque pair IDs are deterministic;
neither the baseline/candidate mapping nor prompt/model identity of the generator
is included in judge inputs. The bundle contains exact pair data, empty review
templates and explicit model-judge requests. A template has `score: null` and empty
evidence lists; it is not a passing review. A provider can potentially infer style
from an output; anonymity is an evidence-handling rule, not a statistical guarantee.

Each completed review must bind `pair_id`, `pair_sha256` and the frozen `judge_id`,
score all six dimensions for both labels, and cite 1..4 exact quotes per score via
JSON pointers. Integers outside 0..10, booleans, fabricated quotes, missing fields,
a different judge and stale paired outputs are rejected. A quoted substring only
proves that the evidence exists, not that the artistic judgment is correct.

```bash
python -m factory.creative_experiment import-review \
  runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json \
  --review <completed-review.json>
```

For `evidence_mode: external` model judgments, also pass `--receipt`. The receipt
must match the frozen independent-critic model/config, exact anonymous input,
judge instructions/schema, sampling policy and output. A human rating cannot be
substituted for a frozen model judge. Human reviews are explicit attestations and
never become production approvals.

## Aggregate every result and preserve regressions

```bash
python -m factory.creative_experiment verify \
  runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json
python -m factory.creative_experiment compare \
  runtime/creative-runs/writer-contract-v1 \
  --seal runtime/creative-seals/writer-contract-v1.json \
  --output runtime/creative-runs/writer-report-v1
```

Missing observations yield `not_run`/`incomplete` and no aggregate score. The CLI
returns 2 for an incomplete or blocked report; it does not turn schema success into
an artistic pass. Successful protocol verification still does not imply a model
ran. `report.json`, `summary.json` and `summary.md` are written to a new directory.

The aggregation order is both label positions → each replicate → each case → the
selected split. Every predetermined replicate has equal weight. Reports preserve
per-case and per-dimension deltas, list regressed cases and identify repeated
output digests. Identical legitimate outputs are retained, not discarded or claimed
as independent evidence of improvement. A reused original call receipt is rejected.
No best-of selection, automatic significance claim or improvement percentage is
computed. A positive observed macro delta with regressed cases is reported as mixed.

Externally reported observations are not independently verified real-model runs.
Source/corpus/model/config/prompt/protocol versions and quote/receipt/output hashes
are kept for audit. Old receipts without required binding fields cannot substantiate
this new protocol; they are not relabelled, rewritten or invalidated in existing
projects. Historical film-evaluation files still belong to the existing evaluator.

## Development and holdout

The checked-in corpus contains six original synthetic cases: four development and
two explicitly named **public holdout-contract fixtures**. They exercise different
genres, all three production profiles, negative controls and conflicting feedback.
They are public and visible during implementation; they are not genuinely unseen
holdout material and their tests establish no artistic improvement.

Real release evaluation needs freshly selected, independently held cases. Freeze
variants/config/rules before revealing outcomes. `--split holdout --allow-holdout`
is an explicit release step, not ordinary tuning. Before any frozen inputs are
exported, the tool reserves each stable case ID for that exact plan and seal name
in the operator-controlled seal directory. A second plan or a renamed seal is
rejected even if no model ran and no final scores were computed. Interrupted
reservations remain in place; only the identical plan/seal may reconcile them.
A completed comparison additionally seals the observed results. Keep this same
trusted directory across runs: editing development cases, changing corpus labels,
seeds, variants or subsets cannot renew a case. Verification is read-only and
requires the original release reservations. Moving an experiment also requires
its operator-controlled seal/ledger. Renaming case IDs or discarding the ledger
cannot make seen material unseen; managing actual exposure remains an operator
responsibility.

Once holdout outcomes inform a revision, retire those cases to development and
use genuinely new held-out inputs. Small synthetic tests do not establish adequate
statistical power, genre coverage or a reliable real-world improvement.

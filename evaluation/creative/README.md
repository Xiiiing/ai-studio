# Frozen creative protocol fixtures

`corpus-v1.json` is an original synthetic, text-only contract corpus. It is not a
reference run, production project, licensed novel adaptation, rendered film or
measured model-quality result. No upstream story/fixture text is copied.

The six cases cover comedy, workplace drama, family drama, suspense, fantasy and
sports; profiles include `compact6`, `ensemble10` and `legacy15`. Every case freezes
both a writer context and a neutral, schema-valid storyboard input. Inputs preserve
stable identities, visible prop ownership, exact shot scopes and explicit human
feedback conflicts. Metadata labels are not sent to generators or judges.

Four development cases can test protocol behavior. The two `public-holdout-*`
cases are publicly exposed tests of the split/consumption mechanism, **not unseen
holdout material**. A release claim requires a genuinely held-out external corpus
and explicitly authorized real observations. All PR validation uses simulation or
synthetic external-receipt fixtures, not real model calls.

`model-config.example.json` intentionally disables a placeholder model. It enables
an entirely offline freeze/verify/not-run-report demonstration and cannot establish
that a model exists, is reachable or produces acceptable work.

See `docs/CREATIVE_AB_PROTOCOL.md` for freeze, import, blind review, comparison,
trust boundaries and explicit non-execution guarantees. V1 is immutable: changes
need a new corpus version and a new trusted seal; do not silently update a frozen
run or cherry-pick the best replicate.

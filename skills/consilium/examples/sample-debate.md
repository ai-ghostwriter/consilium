# Example: a worked debate

This is an illustrative transcript showing the shape of a Consilium run. The
content is abbreviated for readability — a real `debate_state.json` contains the
full text of every turn.

## Command

```bash
python scripts/consilium.py \
  "Should a small team adopt a monorepo or keep separate repos for 6 services?" \
  --judge-mode panel --max-rounds 5
```

## What you see on stderr (the loop trace)

```
[consilium] Round 0 — initial positions (self-answered brainstorming)
[consilium]   invoking Debater A
[consilium]   invoking Debater B
[consilium] Round 1 — confrontation
[consilium]   invoking Debater A
[consilium]   invoking Debater B
[consilium]     panel: disagreement -> 1 reconciliation round
[consilium]     panel: reconciled
[consilium]   judging (panel:haiku+4o-mini (reconciled))
[consilium]     self-scores A=72 B=68 | judge conv=False (61)
[consilium] Round 2 — confrontation
[consilium]   invoking Debater A
[consilium]   invoking Debater B
[consilium]     panel: agreement on first pass
[consilium]   judging (panel:haiku+4o-mini)
[consilium]     self-scores A=88 B=86 | judge conv=True (89)
[consilium] Final synthesis (Opus)
---
[stop: early convergence at round 2 (self-score >= 85 and judge in favor)]
[full transcript: /tmp/consilium_ab12cd/debate_state.json]
```

## What you see on stdout (the deliverable)

```
FINAL DECISION

Adopt a monorepo, but stage it:

1. Start with a single repo using a workspace tool (pnpm/turborepo or Nx).
   Keep the 6 services as packages under /services.
2. Enforce per-package CI: only build/test what changed (affected-graph), so the
   monorepo does not slow down pipelines.
3. Define CODEOWNERS per package to preserve team boundaries.
4. Do NOT share a single deployment unit — keep independent release pipelines per
   service. The monorepo is for code colocation, not coupled deploys.
5. Revisit in 2 quarters: if build times or ownership friction grow, the affected
   packages can be extracted back out with minimal churn.

Rationale: at 6 services and a small team, the coordination cost of separate
repos (cross-repo PRs, version drift, duplicated tooling) outweighs the blast-
radius concerns, provided CI is scoped to changed packages.
```

## Reading the transcript

The full `debate_state.json` has three arrays:

- `turns` — every debater turn, with `critique`, `integration`,
  `revised_position`, `self_score`, `rationale`, and the `raw` model output.
- `verdicts` — one judge verdict per round (`converged`, `convergence_score`,
  `residual_disagreements`, `synthesis_so_far`, `judged_by`).
- `final_decision` — the synthesized deliverable (also printed to stdout).

Plus `stopped_reason`, telling you whether it stopped on convergence or budget.

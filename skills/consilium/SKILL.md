---
name: consilium
description: >
  Orchestrates a structured debate between two peer CLI agents — Claude Code
  (claude -p) and Codex (codex exec) — on a given topic, until convergence. Use
  this skill whenever the user wants Claude and Codex to "confront each other",
  "discuss", "brainstorm together", "find a two-headed solution" or "reach a
  decision" on a technical or strategic problem. Explicit triggers: "have them
  debate", "debate this", "you two decide", "debate loop", "brainstorm topic X".
  Do NOT use for single-author writing tasks or when the user wants a direct
  answer from a single agent.
---

# Consilium — Multi-Agent Debate Loop

This skill launches a debate loop between **two peer debaters** mediated by a
Python orchestrator script. The Claude Code instance that activates this skill is
**NOT a debater**: it is only the launcher. It must start the orchestrator and
report the final decision. This avoids the conflict of interest where one agent
is both participant and judge of its own score.

## Architecture (who does what)

- **Debater A** = `claude -p` (fresh Claude Code instance)
- **Debater B** = `codex exec` (Codex CLI)
- **Judge** = `claude -p --model haiku` (independent third vote, cheap)
- **Final synthesis** = `claude -p --model opus` (high-leverage decision)
- **Orchestrator** = `scripts/consilium.py` (message bus: reads/writes
  `debate_state.json`, composes each round's prompts, applies the stop)

The two debaters **never talk directly**: everything passes through shared
file state.

## Loop stop (triple condition)

1. **Budget** — max 5 rounds (= 10 debater invocations). Hard ceiling.
2. **Self-score** — each round both agents give a 0–100 reconcilability score.
3. **Judge** — Haiku declares whether the positions have converged.

Early stop ONLY if *both* self-scores ≥ 85 **AND** the judge declares
convergence. Otherwise it runs to budget. (The double condition prevents false
convergence from mutual LLM politeness.)

## How to run

1. Ensure `claude` and `codex` are in PATH and authenticated.
2. Launch the orchestrator with the user's topic:

   ```bash
   python scripts/consilium.py "USER TOPIC HERE"
   ```

   Useful flags:
   - `--max-rounds 5` (default)
   - `--threshold 85` (self-score threshold for early stop)
   - `--workdir ./debate_run` (where to save state and logs; default: tempdir)
   - `--repo-path /path/to/repo` (if the debaters must see a codebase)
   - `--lang English` (language of the debate content and final decision)

3. The script prints to stdout **only the final actionable decision** (what the
   user wants) and saves the full transcript to `debate_state.json` in the workdir.

4. Report the final decision to the user. Do not paste the full transcript unless
   explicitly asked: just point to where it is saved.

## Operational notes

- If a debater fails or returns malformed JSON, the orchestrator retries once
  with a "reformat as valid JSON" prompt, then skips the turn, flagging it.
- Round 0 forces each agent to **self-answer** the Socratic brainstorming
  questions (in headless mode there is no human to answer): this is expected,
  not a bug.
- The default judge is Haiku (Claude family: slight affinity possible with
  debater A). For maximum neutrality use `--judge-mode alternate` (Haiku on odd
  rounds, Codex on even), `--judge-mode codex`, or `--judge-mode panel`.

### Panel mode (Haiku + 2nd model)

`--judge-mode panel` uses two judges from different vendors — Haiku (Anthropic,
via `claude -p`) and a second model (e.g. gpt-4o-mini, via a **second**
`codex exec` with the model forced) — judging independently. **No OPENAI_API_KEY**:
the second judge is just another Codex invocation with the model overridden.
Reconciliation is **conditional**, to avoid wasting rounds when they already agree:

1. The two judge separately.
2. If they agree (same yes/no **and** score gap ≤ 15) → verdict = mean. Done.
3. If they disagree → **one** cross round (each sees the other's verdict and revises).
4. If they still disagree → conservative tie-break: convergence only if *both*
   say yes, score = minimum (when in doubt, no convergence is declared).

How to force the second judge's model (two ways):
- `--panel-judge-model gpt-4o-mini` (default) → `codex exec --model gpt-4o-mini`
- `--panel-judge-profile <name>` → `codex exec --profile <name>`, if you defined
  a dedicated profile in `~/.codex/config.toml` (cleaner for repeated use). If
  set, it takes precedence over the model.

If that Codex fails (model not configured, etc.), the panel **degrades
automatically** to a single Haiku judge instead of breaking.

Useful property: the panel is **symmetric** — Haiku shares a vendor with debater A
(Claude), the 2nd model with debater B (Codex). Each judge has the same affinity
with a different debater, so family bias balances out instead of leaning one way.

## Test

Before first real use, validate the logic at zero cost with stubs of the two CLIs:

```bash
bash scripts/test_smoke.sh
```

The smoke test replaces `claude` and `codex` with fake executables that return
canned JSON, and verifies: output parsing (envelope vs raw stdout), early stop,
budget stop, judge alternation, panel reconciliation and final-decision
production. It must print `ALL TESTS PASSED`.

## Requirements

- Python 3.9+ (standard library only — no pip dependencies)
- [`claude`](https://docs.claude.com/en/docs/claude-code) CLI in PATH, authenticated
- [`codex`](https://github.com/openai/codex) CLI in PATH, authenticated
- Models referenced by alias (`haiku`, `opus`); adjust the constants at the top
  of `scripts/consilium.py` if your CLI uses different model names.

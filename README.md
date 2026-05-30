# Consilium 🏛️🤖⚔️🤖

> A structured debate loop between two peer AI agents — **Claude Code** and
> **Codex** — that argue a topic until they converge, refereed by an independent
> judge. *Consilium* (Latin: a council that deliberates) turns two models into a
> deliberating council that hands you one decision.

*[🇮🇹 Versione italiana](README.it.md)*

Two frontier models rarely make the same mistakes. **Consilium** turns that into
an asset: it makes Claude and Codex critique each other's reasoning across
multiple rounds, then has a neutral judge decide when they've genuinely converged
— not just when they're being polite. The output is a single, actionable
decision.

It is packaged as a [Claude Code plugin](https://docs.claude.com/en/docs/claude-code/plugins)
(and skill), but the orchestrator (`skills/consilium/scripts/consilium.py`) is a
standalone Python script you can run from any shell.

## Why use it

- **Reduces single-model blind spots.** Each model attacks the other's weak points.
- **No false convergence.** Stops early only when *both* debaters score the
  reconcilability ≥ 85 **and** an independent judge agrees.
- **Vendor-balanced judging.** The optional `panel` mode pairs an Anthropic judge
  with an OpenAI judge so family bias cancels out.
- **Cheap to validate.** A full smoke test runs offline with stubbed CLIs — no
  API calls, no tokens.
- **Zero dependencies.** Pure Python standard library.

## How it works

```
        ┌─────────────┐         ┌─────────────┐
        │  Debater A  │         │  Debater B  │
        │  claude -p  │         │ codex exec  │
        └──────┬──────┘         └──────┬──────┘
               │   (never talk directly)   │
               └───────────┬───────────────┘
                           ▼
                 debate_state.json  ◄── orchestrator (message bus)
                           ▼
                  ┌─────────────────┐
                  │      Judge      │  haiku / codex / alternate / panel
                  └────────┬────────┘
                           ▼
              Final synthesis (claude -p --model opus)
                           ▼
                  one actionable decision
```

Each round: both debaters critique the opponent, integrate what's valid, emit a
revised position and a self-score. A judge then rules on convergence. Triple stop
condition: **budget** (max rounds) + **self-score** + **judge verdict**.

## Install as a plugin

Consilium is listed in the `codex-coprocessor` marketplace alongside the other
Claude+Codex tools.

```text
# in Claude Code:
/plugin marketplace add ai-ghostwriter/codex-coprocessor
/plugin install consilium@codex-coprocessor
```

## Quick start (standalone script)

```bash
# 1. Make sure both CLIs are installed and authenticated
claude --version
codex --version

# 2. Run a debate
python skills/consilium/scripts/consilium.py "Should we migrate the API from REST to gRPC?"

# 3. (optional) Max-neutrality cross-vendor judging
python skills/consilium/scripts/consilium.py "Postgres vs DynamoDB for this workload?" \
    --judge-mode panel --repo-path /path/to/repo
```

stdout = the final actionable decision. The full transcript is saved to
`debate_state.json` in the work directory.

### Key flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--max-rounds` | `5` | Hard ceiling on debate rounds |
| `--threshold` | `85` | Self-score needed for early stop |
| `--judge-mode` | `haiku` | `haiku` \| `codex` \| `alternate` \| `panel` |
| `--workdir` | tempdir | Where state + logs are written |
| `--repo-path` | none | Codebase the debaters can read |
| `--lang` | `English` | Language of the content and final decision |

See [`skills/consilium/SKILL.md`](skills/consilium/SKILL.md) for the full behavior
reference, including panel-mode reconciliation.

## Requirements

- Python 3.9+ (standard library only)
- [`claude`](https://docs.claude.com/en/docs/claude-code) CLI, authenticated
- [`codex`](https://github.com/openai/codex) CLI, authenticated

## Manual install (skill only, no plugin)

```bash
git clone https://github.com/ai-ghostwriter/consilium.git ~/dev/consilium
ln -s ~/dev/consilium/skills/consilium ~/.claude/skills/consilium
```

Then ask Claude Code to "have Claude and Codex debate X".

## Testing

```bash
bash skills/consilium/scripts/test_smoke.sh   # offline, stubbed CLIs, no tokens spent
```

Expected: `ALL TESTS PASSED`. CI runs this on every push (see
[`.github/workflows/test.yml`](.github/workflows/test.yml)).

## See also

- [`skills/consilium/examples/sample-debate.md`](skills/consilium/examples/sample-debate.md) — a worked transcript.

## License

[MIT](LICENSE)

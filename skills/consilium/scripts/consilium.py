#!/usr/bin/env python3
"""
consilium.py — Orchestrator for a debate loop between two peer CLI agents.

Debater A : claude -p            (Claude Code headless)
Debater B : codex exec           (Codex CLI headless)
Judge     : claude -p --model H  (independent third vote, cheap)
Synthesis : claude -p --model O  (high-leverage final decision)

This script is NOT a debater: it is the message bus. It composes each round's
prompts, invokes the two agents as subprocesses, collects their positions into a
shared state file (debate_state.json) and applies a triple stop condition:
budget (max rounds) + self-score + judge verdict.

Usage:
    python consilium.py "My topic" [--max-rounds 5] [--threshold 85]
                                      [--workdir ./run] [--repo-path .]
                                      [--judge-mode haiku|codex|alternate|panel]
                                      [--lang English]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Model configuration. Opus for the high-leverage final decision, Haiku for the
# cheap classification/judging step.
# --------------------------------------------------------------------------- #
JUDGE_MODEL = "haiku"          # alias accepted by the claude CLI; or full string
SYNTH_MODEL = "opus"
AGENT_TIMEOUT = 600            # seconds per debater invocation
JUDGE_TIMEOUT = 180

# Judge modes:
#   "haiku"    -> always claude -p --model haiku (default, cheap)
#   "codex"    -> always codex exec (different family than debater A)
#   "alternate"-> odd rounds = haiku, even rounds = codex (max neutrality)
#   "panel"    -> Haiku (claude) + a second model (codex with forced model)
#                 judge independently; conditional reconciliation (a single
#                 cross round) only on disagreement; conservative tie-break.
#                 No OPENAI_API_KEY needed: the second judge is just another
#                 codex exec with the model overridden. Fallback: Haiku only if
#                 codex fails.
JUDGE_MODE_DEFAULT = "haiku"

# Cross-vendor panel: the 2nd judge is Codex with the model forced (per-run via
# --model, or a dedicated profile via --profile).
PANEL_JUDGE_MODEL = "gpt-4o-mini"
PANEL_JUDGE_PROFILE: Optional[str] = None   # e.g. "judge4omini" if set in config.toml
PANEL_AGREE_DELTA = 15         # |score_haiku - score_panel| above which = disagreement

DEFAULT_LANG = "English"


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    agent: str                 # "A" | "B"
    round: int
    critique: str = ""
    integration: str = ""
    revised_position: str = ""
    self_score: int = 0
    rationale: str = ""
    raw: str = ""              # raw output, for debugging


@dataclass
class JudgeVerdict:
    round: int
    converged: bool
    convergence_score: int
    residual_disagreements: list[str] = field(default_factory=list)
    synthesis_so_far: str = ""
    judged_by: str = ""


@dataclass
class DebateState:
    topic: str
    started_at: str
    turns: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    final_decision: str = ""
    stopped_reason: str = ""

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Parsing helper: agents sometimes wrap JSON in ``` or add a preamble. We
# defensively extract the first valid JSON object.
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    # strip markdown fences
    if "```" in cleaned:
        parts = cleaned.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                cleaned = p
                break
    # direct attempt
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # fallback: slice from first { to last }
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start: end + 1])
        except json.JSONDecodeError:
            return None
    return None


# --------------------------------------------------------------------------- #
# CLI agent invocation
# --------------------------------------------------------------------------- #
def run_claude(prompt: str, model: Optional[str] = None,
               cwd: Optional[str] = None, timeout: int = AGENT_TIMEOUT) -> str:
    """Invoke claude headless. Returns the final response text."""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:500]}")
    # the --output-format json envelope carries the final text in "result"
    try:
        envelope = json.loads(proc.stdout)
        return envelope.get("result", proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout


def run_codex(prompt: str, cwd: Optional[str] = None,
              timeout: int = AGENT_TIMEOUT,
              model: Optional[str] = None,
              profile: Optional[str] = None) -> str:
    """Invoke codex exec headless. Returns only the agent's final message.

    model   -> force the model for this run (--model flag).
    profile -> load a profile from ~/.codex/config.toml (--profile flag).
    Flags go after 'exec' and before the positional prompt.
    """
    cmd = ["codex", "exec"]
    if profile:
        cmd += ["--profile", profile]
    if model:
        cmd += ["--model", model]
    cmd += ["--skip-git-repo-check", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"codex exit {proc.returncode}: {proc.stderr[:500]}")
    # codex exec streams progress to stderr and prints only the answer to stdout
    return proc.stdout.strip()


def invoke(agent: str, prompt: str, cwd: Optional[str]) -> str:
    return run_claude(prompt, cwd=cwd) if agent == "A" else run_codex(prompt, cwd=cwd)


def invoke_json(agent: str, prompt: str, cwd: Optional[str]) -> dict:
    """Invoke the agent and demand JSON; one retry asking it to reformat."""
    raw = invoke(agent, prompt, cwd)
    parsed = extract_json(raw)
    if parsed is not None:
        parsed["_raw"] = raw
        return parsed
    fix = ("Your previous output was not valid JSON. Re-emit ONLY the requested "
           "JSON, with no surrounding text and no markdown fences.\n\n"
           f"Output to fix:\n{raw[:2000]}")
    raw2 = invoke(agent, fix, cwd)
    parsed = extract_json(raw2) or {}
    parsed["_raw"] = raw2
    return parsed


def judge(prompt: str, judge_mode: str, rnd: int,
          cwd: Optional[str]) -> tuple[dict, str]:
    """Run the judging step with the chosen judge. Returns (parsed_json, judge_name).

    The judge is independent of the debaters. In 'alternate' mode it switches
    family every round to avoid a systematic affinity with debater A.
    """
    if judge_mode == "codex":
        use_codex = True
    elif judge_mode == "alternate":
        use_codex = (rnd % 2 == 0)        # even round -> Codex, odd -> Haiku
    else:                                  # "haiku" or any fallback
        use_codex = False

    if use_codex:
        raw = run_codex(prompt, cwd=cwd, timeout=JUDGE_TIMEOUT)
        name = "codex"
    else:
        raw = run_claude(prompt, model=JUDGE_MODEL, timeout=JUDGE_TIMEOUT)
        name = f"claude:{JUDGE_MODEL}"
    return (extract_json(raw) or {"_raw": raw}), name


# --------------------------------------------------------------------------- #
# Cross-vendor panel (Haiku + 2nd model) with conditional reconciliation.
# The two functions below are PURE (no network): testable in isolation.
# --------------------------------------------------------------------------- #
def verdicts_agree(vh: dict, vo: dict, delta: int = PANEL_AGREE_DELTA) -> bool:
    """True if both judges agree on yes/no AND scores are within delta."""
    same_call = bool(vh.get("converged")) == bool(vo.get("converged"))
    close = abs(int(vh.get("convergence_score", 0) or 0)
                - int(vo.get("convergence_score", 0) or 0)) <= delta
    return same_call and close


def combine_verdicts(vh: dict, vo: dict, conservative: bool = False) -> dict:
    """Merge two verdicts.

    - conservative=False (judges agree): converged = shared value,
      score = mean, disagreements = union.
    - conservative=True  (persistent disagreement): prudent tie-break,
      converged only if BOTH say yes, score = minimum. When in doubt, do not
      declare convergence, to avoid triggering a false stop of the debate.
    """
    sh = int(vh.get("convergence_score", 0) or 0)
    so = int(vo.get("convergence_score", 0) or 0)
    ch, co = bool(vh.get("converged")), bool(vo.get("converged"))
    residual = list(dict.fromkeys(
        (vh.get("residual_disagreements") or []) + (vo.get("residual_disagreements") or [])
    ))
    synthesis = vh.get("synthesis_so_far") or vo.get("synthesis_so_far") or ""
    if conservative:
        return {"converged": ch and co, "convergence_score": min(sh, so),
                "residual_disagreements": residual, "synthesis_so_far": synthesis}
    return {"converged": ch and co, "convergence_score": round((sh + so) / 2),
            "residual_disagreements": residual, "synthesis_so_far": synthesis}


def judge_panel(judge_prompt_text: str, reconcile_builder: Callable[[dict, dict], str],
                judge_h: Callable[[str], dict], judge_o: Callable[[str], dict],
                log: Callable[[str], None]) -> tuple[dict, str]:
    """Panel orchestration. judge_h/judge_o are callables prompt->dict
    (injectable, so tests can pass fakes instead of real calls).
    reconcile_builder(my_verdict, other_verdict) -> reconciliation prompt.
    Returns (combined_verdict, judge_name).
    """
    vh = judge_h(judge_prompt_text)
    vo = judge_o(judge_prompt_text)
    if verdicts_agree(vh, vo):
        log("    panel: agreement on first pass")
        return combine_verdicts(vh, vo), "panel:haiku+4o-mini"

    # Disagreement -> ONE cross reconciliation round
    log("    panel: disagreement -> 1 reconciliation round")
    vh2 = judge_h(reconcile_builder(vh, vo))
    vo2 = judge_o(reconcile_builder(vo, vh))
    if verdicts_agree(vh2, vo2):
        log("    panel: reconciled")
        return combine_verdicts(vh2, vo2), "panel:haiku+4o-mini (reconciled)"

    log("    panel: persistent disagreement -> conservative tie-break")
    return (combine_verdicts(vh2, vo2, conservative=True),
            "panel:haiku+4o-mini (tie-break)")


# --------------------------------------------------------------------------- #
# Prompt builders
# --------------------------------------------------------------------------- #
def seed_prompt(topic: str, lang: str) -> str:
    return f"""You are a debater in a structured two-agent confrontation on the topic:

TOPIC: {topic}

PHASE 0 — INITIAL POSITION.
Engage your Socratic brainstorming instinct, but you are in non-interactive mode:
there is NO human to answer your questions. Therefore:
1. Generate the 3-5 key questions you would ask to frame the problem.
2. Answer each one yourself with explicit, reasonable assumptions.
3. On that basis, formulate your reasoned initial position/solution.

Return ONLY this JSON (no surrounding text, no fences). Write all free-text
field values in {lang}:
{{
  "revised_position": "<your initial, concrete and actionable solution>",
  "key_assumptions": ["<assumption 1>", "<assumption 2>"],
  "open_questions": ["<remaining doubt 1>"],
  "self_score": <integer 0-100: how confident you are this is THE solution>,
  "rationale": "<why>"
}}"""


def debate_prompt(topic: str, agent: str, my_last: str,
                  opp_last: str, history: str, lang: str) -> str:
    return f"""You are Debater {agent} in a structured confrontation on the topic:

TOPIC: {topic}

YOUR PREVIOUS POSITION:
{my_last}

THE OPPONENT'S CURRENT POSITION:
{opp_last}

CONDENSED DEBATE HISTORY:
{history}

THIS ROUND'S TASK:
1. CRITIQUE the opponent's position: find concrete weaknesses, not courtesies.
2. INTEGRATE what is genuinely valid in the opponent and improve your thesis.
3. Produce your REVISED POSITION: an updated, actionable solution.
4. SELF-SCORE 0-100: how reconcilable you believe your revised position and the
   opponent's now are into a single shared solution.
   Be honest: if substantial divergences remain, keep the score low.

Return ONLY this JSON (no surrounding text, no fences). Write all free-text
field values in {lang}:
{{
  "critique": "<concrete critique>",
  "integration": "<what you integrated from the opponent>",
  "revised_position": "<your updated actionable position>",
  "self_score": <integer 0-100>,
  "rationale": "<why that score>"
}}"""


def judge_prompt(topic: str, pos_a: str, pos_b: str, lang: str) -> str:
    return f"""You are an impartial JUDGE of a debate between two agents.
You favor no one: you only assess whether the two positions are CONVERGENT.

TOPIC: {topic}

POSITION A:
{pos_a}

POSITION B:
{pos_b}

Return ONLY this JSON. Write free-text field values in {lang}:
{{
  "converged": <true|false: the two positions are now substantially the same solution>,
  "convergence_score": <integer 0-100>,
  "residual_disagreements": ["<remaining divergence 1>", "..."],
  "synthesis_so_far": "<neutral synthesis of the shared solution that has emerged so far>"
}}"""


def judge_reconcile_prompt(topic: str, pos_a: str, pos_b: str,
                           lang: str) -> Callable[[dict, dict], str]:
    """Return a builder (my_verdict, other_verdict) -> reconciliation prompt,
    with topic/positions already bound."""
    def build(my_v: dict, other_v: dict) -> str:
        return f"""You are a JUDGE on a two-member panel. You already assessed the
convergence of this debate, but your FELLOW judge reached a different conclusion.
Re-assess honestly: you may keep or change your position, but justify it.

TOPIC: {topic}

POSITION A:
{pos_a}

POSITION B:
{pos_b}

YOUR PREVIOUS VERDICT:
{json.dumps(my_v, ensure_ascii=False)}

YOUR COLLEAGUE'S VERDICT:
{json.dumps(other_v, ensure_ascii=False)}

Return ONLY this JSON (same schema as before). Write free-text values in {lang}:
{{
  "converged": <true|false>,
  "convergence_score": <integer 0-100>,
  "residual_disagreements": ["..."],
  "synthesis_so_far": "<neutral synthesis>"
}}"""
    return build


def synthesis_prompt(topic: str, pos_a: str, pos_b: str,
                     verdict: JudgeVerdict, lang: str) -> str:
    return f"""Two agents have debated and converge on this topic:

TOPIC: {topic}

FINAL POSITION A:
{pos_a}

FINAL POSITION B:
{pos_b}

JUDGE'S SYNTHESIS:
{verdict.synthesis_so_far}
Residual disagreements: {verdict.residual_disagreements}

Produce the FINAL ACTIONABLE DECISION: what to do, concretely, as actionable
instructions. No theory, no transcript of the debate. Only the decision and the
operational steps. Write in {lang}."""


def short(text: str, n: int = 700) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " […]"


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def run_debate(topic: str, max_rounds: int, threshold: int,
               workdir: Path, repo_path: Optional[str],
               judge_mode: str = JUDGE_MODE_DEFAULT,
               panel_judge_model: Optional[str] = PANEL_JUDGE_MODEL,
               panel_judge_profile: Optional[str] = PANEL_JUDGE_PROFILE,
               lang: str = DEFAULT_LANG) -> DebateState:
    state = DebateState(topic=topic, started_at=datetime.now().isoformat())
    state_path = workdir / "debate_state.json"

    def log(msg: str) -> None:
        print(f"[consilium] {msg}", file=sys.stderr, flush=True)

    # --- Round 0: independent initial positions (no peeking) ---
    log("Round 0 — initial positions (self-answered brainstorming)")
    last = {"A": "", "B": ""}
    for agent in ("A", "B"):
        log(f"  invoking Debater {agent}")
        res = invoke_json(agent, seed_prompt(topic, lang), repo_path)
        last[agent] = res.get("revised_position", "")
        state.turns.append(asdict(Turn(
            agent=agent, round=0,
            revised_position=last[agent],
            self_score=int(res.get("self_score", 0) or 0),
            rationale=res.get("rationale", ""),
            raw=res.get("_raw", ""),
        )))
    state.save(state_path)

    # --- Round 1..max_rounds: critique + revision + judging ---
    for rnd in range(1, max_rounds + 1):
        log(f"Round {rnd} — confrontation")
        history = "\n".join(
            f"[r{t['round']} {t['agent']}] {short(t['revised_position'], 200)}"
            for t in state.turns
        )
        scores: dict[str, int] = {}
        for agent in ("A", "B"):
            opp = "B" if agent == "A" else "A"
            log(f"  invoking Debater {agent}")
            res = invoke_json(
                agent,
                debate_prompt(topic, agent, last[agent], last[opp], history, lang),
                repo_path,
            )
            last[agent] = res.get("revised_position", last[agent])
            scores[agent] = int(res.get("self_score", 0) or 0)
            state.turns.append(asdict(Turn(
                agent=agent, round=rnd,
                critique=res.get("critique", ""),
                integration=res.get("integration", ""),
                revised_position=last[agent],
                self_score=scores[agent],
                rationale=res.get("rationale", ""),
                raw=res.get("_raw", ""),
            )))
        state.save(state_path)

        # --- Judge: cross-vendor panel, or single/alternating ---
        if judge_mode == "panel":
            jp_text = judge_prompt(topic, last["A"], last["B"], lang)
            rec_builder = judge_reconcile_prompt(topic, last["A"], last["B"], lang)

            def jh(p: str) -> dict:
                return extract_json(run_claude(p, model=JUDGE_MODEL,
                                               timeout=JUDGE_TIMEOUT)) or {}

            def jo(p: str) -> dict:
                # 2nd judge: Codex with the model forced (or a dedicated profile)
                return extract_json(run_codex(
                    p, cwd=repo_path, timeout=JUDGE_TIMEOUT,
                    model=panel_judge_model, profile=panel_judge_profile)) or {}

            try:
                jdata, judge_name = judge_panel(jp_text, rec_builder, jh, jo, log)
            except RuntimeError as e:
                # codex/2nd model unavailable -> degrade to single Haiku
                log(f"  panel unavailable ({e}); degrading to single Haiku")
                jdata, judge_name = judge(jp_text, "haiku", rnd, repo_path)
        else:
            jdata, judge_name = judge(
                judge_prompt(topic, last["A"], last["B"], lang),
                judge_mode, rnd, repo_path,
            )
        log(f"  judging ({judge_name})")
        verdict = JudgeVerdict(
            round=rnd,
            converged=bool(jdata.get("converged", False)),
            convergence_score=int(jdata.get("convergence_score", 0) or 0),
            residual_disagreements=jdata.get("residual_disagreements", []) or [],
            synthesis_so_far=jdata.get("synthesis_so_far", ""),
            judged_by=judge_name,
        )
        state.verdicts.append(asdict(verdict))
        state.save(state_path)
        log(f"    self-scores A={scores['A']} B={scores['B']} | "
            f"judge conv={verdict.converged} ({verdict.convergence_score})")

        # --- Triple stop condition ---
        both_high = scores["A"] >= threshold and scores["B"] >= threshold
        if both_high and verdict.converged:
            state.stopped_reason = (
                f"early convergence at round {rnd} "
                f"(self-score >= {threshold} and judge in favor)"
            )
            break
    else:
        state.stopped_reason = f"budget exhausted ({max_rounds} rounds)"

    # --- Final synthesis (Opus) ---
    log("Final synthesis (Opus)")
    final_verdict = (JudgeVerdict(**state.verdicts[-1])
                     if state.verdicts else JudgeVerdict(0, False, 0))
    state.final_decision = run_claude(
        synthesis_prompt(topic, last["A"], last["B"], final_verdict, lang),
        model=SYNTH_MODEL, timeout=JUDGE_TIMEOUT,
    ).strip()
    state.save(state_path)
    return state


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Debate loop Claude Code <-> Codex")
    ap.add_argument("topic", help="The debate topic")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--threshold", type=int, default=85,
                    help="Self-score threshold for early stop")
    ap.add_argument("--workdir", default=None,
                    help="Folder for state and logs (default: tempdir)")
    ap.add_argument("--repo-path", default=None,
                    help="Codebase the debaters should be able to see")
    ap.add_argument("--judge-mode", default=JUDGE_MODE_DEFAULT,
                    choices=["haiku", "codex", "alternate", "panel"],
                    help="Who judges: haiku (default), codex, "
                         "alternate (haiku/codex on alternating rounds), or panel "
                         "(Haiku + 2nd model via Codex, with reconciliation)")
    ap.add_argument("--panel-judge-model", default=PANEL_JUDGE_MODEL,
                    help="Model of the panel's 2nd judge (forced on codex exec "
                         f"via --model; default: {PANEL_JUDGE_MODEL})")
    ap.add_argument("--panel-judge-profile", default=PANEL_JUDGE_PROFILE,
                    help="Alternative to the model: a codex profile (config.toml) "
                         "for the 2nd judge. If set, takes precedence over the model.")
    ap.add_argument("--lang", default=DEFAULT_LANG,
                    help=f"Language for the debate content and final decision "
                         f"(default: {DEFAULT_LANG})")
    args = ap.parse_args()

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(
        prefix="consilium_"))
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        state = run_debate(args.topic, args.max_rounds, args.threshold,
                           workdir, args.repo_path, args.judge_mode,
                           args.panel_judge_model, args.panel_judge_profile,
                           args.lang)
    except subprocess.TimeoutExpired as e:
        print(f"ERROR: timeout invoking an agent: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Check that 'claude' and 'codex' are in PATH and authenticated.",
              file=sys.stderr)
        return 1

    # stdout = ONLY the final actionable decision (what the user wants)
    print(state.final_decision)
    print(f"\n---\n[stop: {state.stopped_reason}]", file=sys.stderr)
    print(f"[full transcript: {workdir / 'debate_state.json'}]",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

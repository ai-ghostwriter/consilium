#!/usr/bin/env bash
#
# test_smoke.sh — Validate consilium.py WITHOUT calling the real CLIs.
#
# Creates two executable stubs ("claude" and "codex") that respond with canned
# JSON, puts them at the head of PATH, runs a toy debate and verifies that:
#   - JSON parsing holds for both output shapes (envelope vs raw stdout)
#   - the early-stop condition fires with high self-scores + judge OK
#   - debate_state.json is produced and contains the final decision
#   - judge alternation and the cross-vendor panel behave as designed
#   - the pure panel logic (verdicts_agree / combine_verdicts) is correct
#
# Usage:  bash test_smoke.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/consilium.py"
SANDBOX="$(mktemp -d)"
BIN="$SANDBOX/bin"
RUN="$SANDBOX/run"
mkdir -p "$BIN" "$RUN"

cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT

# --- Stub "claude": mimics --output-format json (envelope with "result") ---
# Returns: position/critique with self_score 90, and as judge "converged": true.
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
# Concatenate args to detect the prompt type (debater vs judge vs synthesis).
args="$*"
if echo "$args" | grep -q "FINAL ACTIONABLE DECISION"; then
  # final synthesis: free text, not JSON
  echo "DECISION: 1) do X. 2) configure Y. 3) verify Z."
  exit 0
elif echo "$args" | grep -q "impartial JUDGE"; then
  payload='{"converged": true, "convergence_score": 92, "residual_disagreements": [], "synthesis_so_far": "Shared solution: do X with approach Y."}'
else
  payload='{"critique": "weak point P", "integration": "integrated Q", "revised_position": "Position A: do X with Y.", "self_score": 90, "rationale": "we converge"}'
fi
# Typical envelope of: claude -p --output-format json
printf '{"result": %s}\n' "$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$payload")"
STUB
chmod +x "$BIN/claude"

# --- Stub "codex": mimics codex exec (prints raw JSON to stdout) ---
cat > "$BIN/codex" <<'STUB'
#!/usr/bin/env bash
args="$*"
if echo "$args" | grep -q "impartial JUDGE"; then
  echo '{"converged": true, "convergence_score": 88, "residual_disagreements": [], "synthesis_so_far": "Codex synthesis."}'
else
  echo '{"critique": "objection O", "integration": "integrated R", "revised_position": "Position B: do X with Y, variant.", "self_score": 89, "rationale": "we are aligned"}'
fi
STUB
chmod +x "$BIN/codex"

export PATH="$BIN:$PATH"

echo "=== TEST 1: early stop (high self-scores + judge converged) ==="
OUT="$(python3 "$SCRIPT" "Test topic" \
        --max-rounds 5 --threshold 85 --workdir "$RUN" 2>"$RUN/stderr.log")"
echo "--- stdout (expected final decision) ---"
echo "$OUT"
echo "--- stderr (loop traces) ---"
cat "$RUN/stderr.log"

# --- Assertions ---
fail=0
echo
echo "=== ASSERTIONS ==="

if echo "$OUT" | grep -q "DECISION:"; then
  echo "OK  final decision present on stdout"
else
  echo "FAIL final decision missing"; fail=1
fi

if [ -f "$RUN/debate_state.json" ]; then
  echo "OK  debate_state.json created"
else
  echo "FAIL debate_state.json missing"; fail=1
fi

# Must stop at round 1 (all scores >= 85 and judge converged)
if grep -q "early convergence at round 1" "$RUN/stderr.log"; then
  echo "OK  early stop at round 1 as expected"
else
  echo "FAIL did not stop at round 1"; fail=1
fi

# Verify parsing populated the positions (no empty fields)
if python3 -c "
import json,sys
s=json.load(open('$RUN/debate_state.json'))
assert s['final_decision'].strip(), 'empty decision'
assert any(t['revised_position'] for t in s['turns']), 'empty positions'
assert s['verdicts'] and s['verdicts'][0]['converged'] is True, 'wrong verdict'
print('OK  internal state coherent (positions + verdict populated)')
"; then :; else echo "FAIL internal state incoherent"; fail=1; fi

echo
echo "=== TEST 2: panel mode (--judge-mode panel, fresh stubs) ==="
RUNP="$RUN/panel"; mkdir -p "$RUNP"
# Stubs still fresh: claude judge -> converged true score 92,
# codex judge (2nd model) -> converged true score 88. |92-88|<=15 => agreement.
python3 "$SCRIPT" "Test topic" --max-rounds 2 \
        --judge-mode panel --panel-judge-model gpt-4o-mini \
        --workdir "$RUNP" >"$RUNP/out.txt" 2>"$RUNP/stderr.log" || true

echo "--- panel traces ---"
grep -E "panel|judging" "$RUNP/stderr.log" || true
if grep -q "panel: agreement on first pass" "$RUNP/stderr.log"; then
  echo "OK  panel: both judges agree on first pass (no extra round)"
else
  echo "FAIL panel did not record the immediate agreement"; fail=1
fi
if grep -q "judging (panel:haiku+4o-mini)" "$RUNP/stderr.log"; then
  echo "OK  verdict attributed to the panel"
else
  echo "FAIL panel verdict not attributed"; fail=1
fi
# Verify in state that the combined score is the mean (92+88)/2 = 90
if python3 -c "
import json
s=json.load(open('$RUNP/debate_state.json'))
v=s['verdicts'][0]
assert 'panel' in v['judged_by'], v['judged_by']
assert v['convergence_score'] == 90, v['convergence_score']
print('OK  combined score = mean of the two judges (90)')
"; then :; else echo "FAIL panel combination wrong"; fail=1; fi

echo
echo "=== TEST 3: alternating judge (--judge-mode alternate) ==="
RUN2="$RUN/alt"; mkdir -p "$RUN2"
# Lower the stub scores to force more rounds and see the alternation.
sed -i.bak 's/"self_score": 90/"self_score": 50/' "$BIN/claude"
sed -i.bak 's/"self_score": 89/"self_score": 50/' "$BIN/codex"
# And make the judge say "not converged" to reach the budget.
sed -i.bak 's/"converged": true/"converged": false/' "$BIN/claude"
sed -i.bak 's/"converged": true/"converged": false/' "$BIN/codex"
rm -f "$BIN"/*.bak

python3 "$SCRIPT" "Test topic" --max-rounds 2 \
        --judge-mode alternate --workdir "$RUN2" >/dev/null 2>"$RUN2/stderr.log" || true

echo "--- judges used per round ---"
grep "judging" "$RUN2/stderr.log" || true
# round 1 (odd) -> claude:haiku ; round 2 (even) -> codex
if grep -q "judging (claude:haiku)" "$RUN2/stderr.log" \
   && grep -q "judging (codex)" "$RUN2/stderr.log"; then
  echo "OK  judge alternation verified (haiku round 1, codex round 2)"
else
  echo "FAIL judge alternation not detected"; fail=1
fi

if grep -q "budget exhausted" "$RUN2/stderr.log"; then
  echo "OK  budget stop when there is no convergence"
else
  echo "FAIL did not stop on budget"; fail=1
fi

echo
echo "=== TEST 4: pure panel logic (verdicts_agree + combine_verdicts) ==="
if python3 -c "
import sys
sys.path.insert(0, '$HERE')
from consilium import verdicts_agree, combine_verdicts

# Agreement case: same yes/no, close scores -> agree True, mean
a = {'converged': True,  'convergence_score': 90, 'residual_disagreements': ['x']}
b = {'converged': True,  'convergence_score': 82, 'residual_disagreements': ['y']}
assert verdicts_agree(a, b, 15) is True, 'should agree'
c = combine_verdicts(a, b)
assert c['converged'] is True and c['convergence_score'] == 86, c
assert set(c['residual_disagreements']) == {'x', 'y'}, 'union of disagreements'

# Disagreement on yes/no -> agree False
d = {'converged': False, 'convergence_score': 88}
assert verdicts_agree(a, d, 15) is False, 'disagree on yes/no'

# Scores far apart -> agree False
e = {'converged': True,  'convergence_score': 50}
assert verdicts_agree(a, e, 15) is False, 'scores too far apart'

# Conservative tie-break: one yes one no -> converged False, score = min
t = combine_verdicts(a, d, conservative=True)
assert t['converged'] is False and t['convergence_score'] == 88, t
print('OK  verdicts_agree and combine_verdicts correct (agree, disagree, tie-break)')
"; then :; else echo "FAIL panel logic wrong"; fail=1; fi

echo
if [ "$fail" -eq 0 ]; then
  echo "########## ALL TESTS PASSED ##########"
else
  echo "########## THERE ARE FAILURES (see above) ##########"; exit 1
fi

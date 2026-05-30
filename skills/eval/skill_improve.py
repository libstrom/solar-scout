"""
skill_improve.py — Autoresearch loop for Claude Code skills.

Inspired by karpathy/autoresearch: evaluate a skill against binary assertions,
make one targeted improvement to the skill.md, keep if score goes up, revert if not.
Loops autonomously until perfect score or interrupted.

Usage:
    python skills/eval/skill_improve.py --skill skills/engineering/scan-debug/SKILL.md
    python skills/eval/skill_improve.py --skill skills/engineering/scan-debug/SKILL.md --eval skills/engineering/scan-debug/eval.json
    python skills/eval/skill_improve.py --skill skills/engineering/scan-debug/SKILL.md --dry-run
    python skills/eval/skill_improve.py --skill skills/engineering/scan-debug/SKILL.md --max-iterations 20

Loop logic (identical to Karpathy's autoresearch):
    1. Score current skill against all binary assertions
    2. If perfect (1.0): done
    3. Ask Claude to make ONE change to skill.md targeting the worst failures
    4. Re-score
    5. If improved: git commit and continue
    6. If not: git revert and try a different change
    7. Go to 1
"""

import os
import json
import time
import subprocess
import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [skill-improve] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("skill_improve")

ANTHROPIC_API_KEY = (
    os.getenv("SOLAR_SCOUT_ANTHROPIC_KEY")
    or os.getenv("ANTHROPIC_API_KEY")
    or ""
)

# Haiku for both skill simulation and assertion checks — fast and cheap
EVAL_MODEL   = "claude-haiku-4-5-20251001"
# Sonnet for skill improvements — needs to reason well about what to change
IMPROVE_MODEL = "claude-sonnet-4-6"


# ── Data types ──────────────────────────────────────────────────────────────────

@dataclass
class AssertionResult:
    id: str
    check: str
    passed: bool
    reasoning: str = ""


@dataclass
class TestResult:
    test_id: str
    prompt: str
    output: str
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def total(self) -> int:
        return len(self.assertions)

    @property
    def failures(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]


@dataclass
class EvalResult:
    test_results: list[TestResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        total = sum(r.total for r in self.test_results)
        passed = sum(r.passed for r in self.test_results)
        return passed / total if total > 0 else 0.0

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.test_results)

    @property
    def total(self) -> int:
        return sum(r.total for r in self.test_results)

    @property
    def failures(self) -> list[AssertionResult]:
        result = []
        for tr in self.test_results:
            result.extend(tr.failures)
        return result


# ── Core functions ──────────────────────────────────────────────────────────────

def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "Set SOLAR_SCOUT_ANTHROPIC_KEY or ANTHROPIC_API_KEY env var"
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def run_skill(skill_content: str, test_prompt: str) -> str:
    """Simulate running a skill by feeding it to Claude as a system prompt."""
    client = _client()
    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=1500,
        system=(
            "You are Claude Code executing the following skill. "
            "Follow its instructions precisely and produce the output it describes.\n\n"
            f"<skill>\n{skill_content}\n</skill>"
        ),
        messages=[{"role": "user", "content": test_prompt}],
    )
    return response.content[0].text


def check_assertion(output: str, assertion: dict) -> AssertionResult:
    """Check a binary assertion against skill output using Claude Haiku."""
    client = _client()
    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=100,
        system=(
            "You are a precise binary evaluator. "
            "Given a text output and an assertion, determine if the assertion is TRUE or FALSE. "
            "Answer with exactly one word: 'true' or 'false', followed by a brief reason."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"<output>\n{output}\n</output>\n\n"
                f"Assertion: {assertion['check']}\n\n"
                "Is this assertion TRUE or FALSE about the output above? "
                "Start your answer with 'true' or 'false'."
            ),
        }],
    )
    text = response.content[0].text.strip().lower()
    passed = text.startswith("true")
    reasoning = text[5:].strip(" :.") if len(text) > 5 else ""
    return AssertionResult(
        id=assertion["id"],
        check=assertion["check"],
        passed=passed,
        reasoning=reasoning,
    )


def evaluate_skill(skill_path: str, eval_data: dict, verbose: bool = False) -> EvalResult:
    """Run all tests and check all assertions. Returns full EvalResult."""
    skill_content = Path(skill_path).read_text()
    result = EvalResult()

    for test in eval_data["tests"]:
        _log.info("Running test %s: %s", test["id"], test["prompt"][:60])
        output = run_skill(skill_content, test["prompt"])

        if verbose:
            print(f"\n{'─'*60}")
            print(f"TEST {test['id']}: {test['prompt']}")
            print(f"{'─'*60}")
            print(output)
            print()

        tr = TestResult(test_id=test["id"], prompt=test["prompt"], output=output)
        for assertion in test["assertions"]:
            ar = check_assertion(output, assertion)
            tr.assertions.append(ar)
            icon = "✓" if ar.passed else "✗"
            _log.info("  %s %s: %s", icon, ar.id, assertion["check"][:70])

        result.test_results.append(tr)
        _log.info("  Test %s: %d/%d passed", test["id"], tr.passed, tr.total)

    return result


def improve_skill(skill_path: str, failures: list[AssertionResult], iteration: int) -> str:
    """Ask Claude Sonnet to make ONE targeted improvement to skill.md."""
    skill_content = Path(skill_path).read_text()
    client = _client()

    failure_text = "\n".join(
        f"- [{f.id}] FAILED: {f.check}" + (f" (reason: {f.reasoning})" if f.reasoning else "")
        for f in failures
    )

    response = client.messages.create(
        model=IMPROVE_MODEL,
        max_tokens=3000,
        system=(
            "You are improving a Claude Code skill file (skill.md). "
            "Make exactly ONE targeted change to address the failing assertions. "
            "Be surgical — add a rule, clarify an instruction, or add an example. "
            "Do not rewrite the whole file. Return the COMPLETE updated skill.md content."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Iteration {iteration}. This skill.md is failing these assertions:\n\n"
                f"{failure_text}\n\n"
                f"Current skill.md:\n\n{skill_content}\n\n"
                "Make ONE specific change to fix as many of these failures as possible. "
                "Return the complete updated skill.md."
            ),
        }],
    )
    return response.content[0].text


def git_commit(message: str, file_path: str) -> bool:
    result = subprocess.run(
        ["git", "add", file_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True
    )
    return result.returncode == 0


def git_revert_file(file_path: str) -> None:
    subprocess.run(["git", "checkout", "--", file_path], capture_output=True)


def print_scoreboard(result: EvalResult, iteration: int) -> None:
    bar = "█" * int(result.score * 20) + "░" * (20 - int(result.score * 20))
    print(f"\n{'═'*60}")
    print(f"  Iteration {iteration:>3}  |  [{bar}]  {result.passed}/{result.total}  ({result.score:.1%})")
    print(f"{'═'*60}")
    if result.failures:
        print("  Failing assertions:")
        for f in result.failures:
            print(f"  ✗ [{f.id}] {f.check[:70]}")
    print()


# ── Autoresearch loop ───────────────────────────────────────────────────────────

def run_loop(
    skill_path: str,
    eval_path: str,
    max_iterations: int = 50,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """
    Karpathy autoresearch loop applied to skill.md:

    1. Score current skill
    2. If perfect: done
    3. Make ONE improvement
    4. Re-score
    5. If better: commit + continue
    6. If not: revert + try again
    7. Never ask for permission. Loop until perfect or interrupted.
    """
    eval_data = json.loads(Path(eval_path).read_text())
    skill_name = eval_data.get("skill", Path(skill_path).parent.name)

    print(f"\n{'═'*60}")
    print(f"  SKILL AUTORESEARCH LOOP")
    print(f"  Skill:  {skill_path}")
    print(f"  Eval:   {eval_path}")
    print(f"  Model (eval):    {EVAL_MODEL}")
    print(f"  Model (improve): {IMPROVE_MODEL}")
    print(f"  Max iterations:  {max_iterations}")
    print(f"  Dry run:         {dry_run}")
    print(f"{'═'*60}\n")

    _log.info("Scoring baseline...")
    result = evaluate_skill(skill_path, eval_data, verbose=verbose)
    print_scoreboard(result, iteration=0)

    if result.score >= 1.0:
        print("✓ Already at perfect score. Nothing to improve.")
        return

    history: list[tuple[int, float]] = [(0, result.score)]

    for i in range(1, max_iterations + 1):
        _log.info("Iteration %d — improving skill (targeting %d failures)...", i, len(result.failures))

        # Generate improved skill content
        new_content = improve_skill(skill_path, result.failures, i)

        if dry_run:
            print(f"\n[DRY RUN] Would write new skill.md (iteration {i}):")
            print(new_content[:500] + "..." if len(new_content) > 500 else new_content)
            print("\n[DRY RUN] Stopping after first improvement suggestion.")
            return

        # Write the improved skill
        Path(skill_path).write_text(new_content)

        # Re-score
        _log.info("Re-scoring after improvement...")
        new_result = evaluate_skill(skill_path, eval_data, verbose=verbose)
        print_scoreboard(new_result, iteration=i)

        if new_result.score > result.score:
            # Keep — commit
            delta = new_result.score - result.score
            msg = (
                f"skill({skill_name}): auto-improve iteration {i} "
                f"{result.score:.1%} → {new_result.score:.1%} (+{delta:.1%})\n\n"
                f"Fixed assertions:\n" +
                "\n".join(
                    f"- [{f.id}] {f.check}"
                    for f in result.failures
                    if f.id not in {nf.id for nf in new_result.failures}
                )
            )
            committed = git_commit(msg, skill_path)
            status = "committed" if committed else "written (git commit failed)"
            _log.info("✓ Score improved %.1f%% → %.1f%%. Change %s.", result.score * 100, new_result.score * 100, status)
            result = new_result
            history.append((i, result.score))

            if result.score >= 1.0:
                print("\n🎉 Perfect score reached!")
                break
        else:
            # Revert — try a different change next iteration
            git_revert_file(skill_path)
            _log.info("✗ No improvement (%.1f%% vs %.1f%%). Reverted.", new_result.score * 100, result.score * 100)
            history.append((i, result.score))

        # Brief pause to avoid hammering the API
        time.sleep(2)

    # Final summary
    print(f"\n{'═'*60}")
    print("  FINAL SUMMARY")
    print(f"{'═'*60}")
    print(f"  Iterations: {len(history) - 1}")
    print(f"  Baseline:   {history[0][1]:.1%}")
    print(f"  Final:      {history[-1][1]:.1%}")
    if history[-1][1] >= 1.0:
        print("  Result:     ✓ Perfect score!")
    elif history[-1][1] > history[0][1]:
        print(f"  Result:     ↑ Improved by {history[-1][1] - history[0][1]:.1%}")
    else:
        print("  Result:     ↔ No net improvement (skill may need manual review)")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Autoresearch loop for Claude Code skills (Karpathy pattern)"
    )
    parser.add_argument(
        "--skill", required=True,
        help="Path to SKILL.md file to improve"
    )
    parser.add_argument(
        "--eval",
        help="Path to eval.json (default: same directory as SKILL.md)"
    )
    parser.add_argument(
        "--max-iterations", type=int, default=50,
        help="Maximum improvement iterations (default: 50)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Score and show one improvement suggestion, but don't write or commit"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print full skill output for each test"
    )
    args = parser.parse_args()

    skill_path = args.skill
    eval_path = args.eval or str(Path(skill_path).parent / "eval.json")

    if not Path(skill_path).exists():
        raise SystemExit(f"Skill file not found: {skill_path}")
    if not Path(eval_path).exists():
        raise SystemExit(
            f"Eval file not found: {eval_path}\n"
            "Create one with 5 test prompts × 5 binary assertions each."
        )

    run_loop(
        skill_path=skill_path,
        eval_path=eval_path,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

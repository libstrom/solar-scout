#!/usr/bin/env python3
"""
Auto-improvement loop för Claude Code skills — Karpathy-style.

Läser skill.md → kör testprompts → kontrollerar binära assertions →
förbättrar skill.md → committar om bättre, revertar om sämre.
Körs tills perfect score eller --max-rounds nådd.

Användning:
    cd /home/user/solar-scout
    python .claude/commands/eval/loop.py scan-debug
    python .claude/commands/eval/loop.py leads-now --max-rounds 30
"""

import sys
import json
import subprocess
import argparse
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: pip install anthropic", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent.parent.parent  # solar-scout/
COMMANDS_DIR = Path(__file__).parent.parent              # .claude/commands/
EVAL_DIR = Path(__file__).parent                         # .claude/commands/eval/

JUDGE_MODEL = "claude-haiku-4-5-20251001"   # Snabb + billig för assertion-checks
IMPROVE_MODEL = "claude-sonnet-4-6"          # Klok för skill-förbättringar


# ── Kör skill ─────────────────────────────────────────────────────────────────

def run_skill(skill_content: str, prompt: str, client: anthropic.Anthropic) -> str:
    msg = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=800,
        system=(
            "Du är en expert-assistent i Solar Scout (en solpanels-lead-scanning-app). "
            "Följ dessa instruktioner exakt:\n\n" + skill_content
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── Kontrollera assertion ──────────────────────────────────────────────────────

def check_assertion(output: str, assertion: str, client: anthropic.Anthropic) -> bool:
    msg = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=5,
        system="Du är en strikt binär utvärderare. Svara BARA med JA eller NEJ.",
        messages=[{
            "role": "user",
            "content": (
                f"Output att utvärdera:\n{output}\n\n"
                f"Assertion: {assertion}\n\n"
                f"Uppfyller outputen denna assertion? Svara JA eller NEJ."
            ),
        }],
    )
    return msg.content[0].text.strip().upper().startswith("J")


# ── Poängsätt skill ────────────────────────────────────────────────────────────

def score_skill(
    skill_content: str,
    test_cases: list,
    client: anthropic.Anthropic,
) -> tuple[float, list]:
    total = 0
    passed = 0
    results = []

    for tc in test_cases:
        output = run_skill(skill_content, tc["prompt"], client)
        tc_result = {
            "id": tc["id"],
            "prompt": tc["prompt"],
            "output_preview": output[:300],
            "assertions": [],
        }
        for assertion in tc["assertions"]:
            ok = check_assertion(output, assertion, client)
            tc_result["assertions"].append({"text": assertion, "passed": ok})
            total += 1
            if ok:
                passed += 1
        results.append(tc_result)

    score = passed / total if total > 0 else 0.0
    return score, results


# ── Förbättra skill ────────────────────────────────────────────────────────────

def suggest_improvement(
    skill_content: str,
    results: list,
    client: anthropic.Anthropic,
) -> str:
    failures = []
    for tc in results:
        for a in tc["assertions"]:
            if not a["passed"]:
                failures.append(
                    f"Testprompt: '{tc['prompt']}'\n"
                    f"Missad assertion: '{a['text']}'\n"
                    f"Output-preview: '{tc['output_preview'][:150]}'"
                )
    if not failures:
        return skill_content

    failure_text = "\n\n---\n\n".join(failures[:6])

    msg = client.messages.create(
        model=IMPROVE_MODEL,
        max_tokens=3000,
        system=(
            "Du förbättrar Claude Code skill-filer (Markdown med YAML frontmatter). "
            "Gör EXAKT EN riktad förbättring — lägg till ett steg, förtydliga ett steg, "
            "eller lägg till ett konkret exempel. Returnera hela filen komplett. "
            "Ingen förklaring, bara filen."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Skill-fil:\n\n```markdown\n{skill_content}\n```\n\n"
                f"Dessa assertions misslyckades:\n\n{failure_text}\n\n"
                f"Gör EXAKT EN förbättring för att fixa dessa. "
                f"Returnera hela skill-filen komplett."
            ),
        }],
    )
    improved = msg.content[0].text.strip()
    # Rensa bort markdown-kodblock om modellen la till dem
    if improved.startswith("```"):
        lines = improved.split("\n")
        improved = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return improved


# ── Git-hjälpare ───────────────────────────────────────────────────────────────

def git_commit(skill_path: Path, round_num: int, score: float) -> None:
    subprocess.run(["git", "add", str(skill_path)], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m",
         f"skill-eval: improve {skill_path.name} round={round_num} score={score:.1%}"],
        cwd=REPO_ROOT, check=True,
    )


def git_reset(skill_path: Path) -> None:
    subprocess.run(
        ["git", "checkout", "HEAD", "--", str(skill_path)],
        cwd=REPO_ROOT, check=True,
    )


# ── Skriv ut resultat ──────────────────────────────────────────────────────────

def print_results(results: list, score: float, total: int, passed: int) -> None:
    print(f"  Score: {score:.1%}  ({passed}/{total} assertions passerade)")
    for tc in results:
        failed = [a["text"] for a in tc["assertions"] if not a["passed"]]
        if failed:
            print(f"  ❌ [{tc['id']}] Misslyckade:")
            for f in failed:
                print(f"       • {f}")


# ── Huvud-loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Karpathy-style skill self-improvement loop")
    parser.add_argument("skill", help="Skill-namn (t.ex. scan-debug)")
    parser.add_argument("--max-rounds", type=int, default=25)
    parser.add_argument("--no-commit", action="store_true", help="Testa utan att committa")
    args = parser.parse_args()

    skill_name = args.skill
    skill_path = COMMANDS_DIR / f"{skill_name}.md"
    eval_path = EVAL_DIR / skill_name / "eval.json"

    if not skill_path.exists():
        print(f"ERROR: Skill-fil saknas: {skill_path}", file=sys.stderr)
        sys.exit(1)
    if not eval_path.exists():
        print(f"ERROR: Eval-fil saknas: {eval_path}", file=sys.stderr)
        sys.exit(1)

    eval_data = json.loads(eval_path.read_text())
    test_cases = eval_data["test_cases"]
    threshold = eval_data.get("passing_threshold", 1.0)
    total_assertions = sum(len(tc["assertions"]) for tc in test_cases)

    client = anthropic.Anthropic()  # Hämtar ANTHROPIC_API_KEY från env

    print(f"\n🔄  Auto-förbättrar skill: {skill_name}")
    print(f"📊  {len(test_cases)} testfall × {total_assertions // len(test_cases)} assertions = {total_assertions} totalt")
    print(f"🎯  Tröskelvärde: {threshold:.0%}")
    print(f"🔁  Max rundor: {args.max_rounds}")
    print("=" * 60)
    print("Tryck Ctrl+C för att avbryta\n")

    best_score = 0.0
    best_round = 0

    for round_num in range(1, args.max_rounds + 1):
        print(f"\n── Runda {round_num} {'─' * 40}")
        t0 = time.time()

        skill_content = skill_path.read_text()
        score, results = score_skill(skill_content, test_cases, client)
        passed = sum(
            1 for tc in results for a in tc["assertions"] if a["passed"]
        )

        print_results(results, score, total_assertions, passed)
        print(f"  ⏱  {time.time() - t0:.1f}s")

        if score >= threshold:
            print(f"\n✅  Perfect score ({score:.1%})! Skill är fullt optimerad.")
            if not args.no_commit:
                git_commit(skill_path, round_num, score)
            break

        if score > best_score:
            best_score = score
            best_round = round_num
            print(f"  ⭐  Ny bästnotering!")

        # Föreslå förbättring
        print(f"  🔧  Genererar förbättring...")
        new_content = suggest_improvement(skill_content, results, client)

        if new_content.strip() == skill_content.strip():
            print(f"  ⚠️   Ingen ändring genererades — stoppar loopen.")
            break

        # Skriv ny version och re-score
        skill_path.write_text(new_content)
        new_score, _ = score_skill(new_content, test_cases, client)
        new_passed = round(new_score * total_assertions)

        if new_score >= score:
            if not args.no_commit:
                git_commit(skill_path, round_num, new_score)
            print(f"  ✅  Behållen: {score:.1%} → {new_score:.1%} (+{new_passed - passed} assertions)")
        else:
            git_reset(skill_path)
            print(f"  ↩️   Revertad: {score:.1%} → {new_score:.1%} (sämre — testar ny ändring nästa runda)")

    print(f"\n🏁  Klar. Bästa score: {best_score:.1%} (runda {best_round})")
    if best_score < threshold:
        print(f"    Kör fler rundor: python eval/loop.py {skill_name} --max-rounds 50")


if __name__ == "__main__":
    main()

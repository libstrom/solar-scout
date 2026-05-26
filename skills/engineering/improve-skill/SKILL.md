# improve-skill

Karpathy-style autonomous self-improvement loop for Claude Code skills.
Makes ONE change per iteration, scores it, commits if better, reverts if not.
Runs overnight — never stops until 100% pass rate or manually interrupted.

## Protocol: NEVER STOP

Once the loop starts:
- Do NOT pause to ask the user if you should continue
- Do NOT wait for confirmation between iterations
- Do NOT stop unless: (a) 100% pass rate, (b) 10 consecutive reversions, or (c) user interrupts (Ctrl+C)
- The user is likely asleep — keep working

## Loop algorithm

Repeat:

```
1. READ    skill_path/SKILL.md + skill_path/eval.json
2. TEST    for each test in eval.json:
             prompt = SKILL.md content + "\n\n---\n\n" + test.prompt
             invoke Agent with that prompt
             for each assertion: true if output satisfies the check, else false
           score = total_true / total_assertions
3. DECIDE  if score == 1.0 → DONE (report final score, stop)
           if iterations >= 10 and no improvement in last 5 → STUCK (report, stop)
4. IMPROVE if score < 1.0:
             find the assertions that fail most often
             make ONE surgical edit to SKILL.md addressing the most common failure
             do NOT rewrite the whole skill — edit one sentence or add one rule
5. RE-TEST run all tests again with the updated SKILL.md
6. KEEP    if new_score > old_score:
             git add skill_path/SKILL.md
             git commit -m "skill(NAME): pass rate N/25 (X%)"
           REVERT if new_score <= old_score:
             git checkout -- skill_path/SKILL.md
7. LOG     print iteration summary
8. LOOP    go to step 1
```

## Binary assertion evaluation

For each assertion string, evaluate: does the skill output satisfy this statement?
- Answer ONLY yes or no — no partial credit
- Count "yes" answers → pass_rate = yes_count / total_assertions
- A skill "passes" at 100% (25/25 for a standard 5×5 eval.json)

## Surgical edit examples

Good (targeted):
- "Add 'Do not recommend Stockholm or Göteborg as the first city' to the decision tree"
- "Add 'Response must be under 200 words' to the output format section"
- "Clarify that /scan-debug should be mentioned when 0 leads is reported"

Bad (too broad):
- "Rewrite the entire decision tree"
- "Restructure the skill from scratch"

## Iteration log format (print after each iteration)

```
--- Iteration N ---
Score: X/25 (Z%)
Previously: Y/25
Failing: [assertion IDs that failed]
Change: [one-line description of what was changed, or NONE]
Decision: KEPT | REVERTED | DONE | STUCK
```

## Finding skill paths

Given a skill name like "leads-now", find:
- SKILL.md: `skills/engineering/leads-now/SKILL.md` or `skills/productivity/leads-now/SKILL.md`
- eval.json: same directory as SKILL.md

Use `find . -name "SKILL.md" -path "*leads-now*"` if unsure of location.

## Invocation examples

```
/improve-skill leads-now
/improve-skill scan-debug
```

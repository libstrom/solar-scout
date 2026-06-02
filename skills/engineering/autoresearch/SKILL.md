---
name: autoresearch
description: Run the Karpathy-style autoresearch loop on a skill. Scores the skill against binary assertions, makes ONE targeted improvement, keeps if better, reverts if not. Repeats until perfect score or max iterations reached.
---

# autoresearch

Run the Karpathy autoresearch loop (`skills/eval/skill_improve.py`) on a SKILL.md to improve it autonomously.

## How it works

```
1. Score current SKILL.md against all binary assertions (Haiku)
2. If perfect (1.0): done
3. Ask Opus 4.8 to make ONE targeted change
4. Re-score
5. If improved: git commit, continue
6. If not: git revert, try again
7. Go to 1
```

## When to use

- `/autoresearch` — pick a skill to improve interactively
- `/autoresearch leads-now` — improve the leads-now skill
- `/autoresearch scan-debug` — improve the scan-debug skill

## Running the loop

The user has invoked `/autoresearch`. Execute:

```bash
python skills/eval/skill_improve.py \
  --skill skills/engineering/<SKILL_NAME>/SKILL.md \
  --max-iterations 10
```

### Step 1: Determine which skill to run on

If the user provided a skill name as args, use it directly.
Otherwise, list available skills and ask the user to pick one:

```bash
ls skills/engineering/
```

Show the list and ask: "Vilken skill vill du förbättra?"

### Step 2: Verify the API key is available

```bash
python -c "
import sys; sys.path.insert(0, '.')
from skills.eval.skill_improve import _read_api_key
key = _read_api_key()
if key:
    print(f'API key found: {key[:12]}...')
else:
    print('ERROR: No API key found')
    sys.exit(1)
"
```

If the key is missing, tell the user to set `SOLAR_SCOUT_ANTHROPIC_KEY` or `ANTHROPIC_API_KEY`.

### Step 3: Run the loop

```bash
python skills/eval/skill_improve.py \
  --skill skills/engineering/<SKILL_NAME>/SKILL.md \
  --max-iterations 10
```

Stream the output to the user. The loop will:
- Print scores after each iteration
- Show which assertion failed
- Show the change it made
- Commit improvements to git

### Step 4: Report results

After the loop completes (or is interrupted), report:
- Starting score vs final score
- Number of iterations run
- Whether git commits were made (run `git log --oneline -5`)

## Available skills to improve

```
skills/engineering/diagnose/
skills/engineering/leads-now/
skills/engineering/scan-debug/
skills/engineering/ship-pr/
skills/engineering/tdd/
skills/engineering/to-issues/
skills/engineering/to-prd/
skills/engineering/triage/
```

## Dry run (preview only)

Add `--dry-run` to see what changes would be made without committing:

```bash
python skills/eval/skill_improve.py \
  --skill skills/engineering/<SKILL>/SKILL.md \
  --dry-run
```

## Notes

- The loop uses `claude-haiku-4-5-20251001` for scoring (fast/cheap)
- The loop uses `claude-opus-4-8` for improvements (best reasoning)
- Each iteration takes ~15-30 seconds
- Improvements are git-committed automatically if the score goes up
- Max 10 iterations by default (override with `--max-iterations N`)

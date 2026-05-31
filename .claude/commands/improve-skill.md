---
name: improve-skill
description: Karpathy-style autonomous self-improvement loop for a Claude Code skill. Reads eval.json binary assertions, tests the skill, makes ONE surgical change per iteration, commits if pass rate improves, reverts if not. Never stops until 100% pass rate. Use when you want to improve leads-now, scan-debug, or any skill that has an eval.json file.
---

Run the improve-skill skill from skills/engineering/improve-skill/SKILL.md.

Target skill: $ARGUMENTS

Protocol:
1. Find SKILL.md and eval.json for the target skill name
2. Run the self-improvement loop — never stop between iterations
3. Log each iteration's score and decision
4. Stop only when 100% pass rate, 10 consecutive reversions, or user interrupts

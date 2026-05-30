---
name: auto-research
description: Karpathy autoresearch loop — kör skills/eval/skill_improve.py autonomt mot en skills eval.json. Använder Claude Haiku för evaluation och Sonnet för förbättringar. Loopar tills 100% pass rate, 50 iterationer, eller Ctrl+C. Snabbare och billigare än /improve-skill (ingen human i loopen). Usage: /auto-research leads-now
---

Run the autoresearch loop using the Python implementation at skills/eval/skill_improve.py.

Target skill name: $ARGUMENTS

Steps:
1. Resolve the skill path: find skills/engineering/$ARGUMENTS/SKILL.md (or skills/productivity/$ARGUMENTS/SKILL.md)
2. Verify eval.json exists in the same directory
3. Run in terminal:
   ```
   python skills/eval/skill_improve.py --skill <skill_path>
   ```
4. Stream output to the user — show each iteration's score and what changed
5. When loop finishes, report final score and total iterations

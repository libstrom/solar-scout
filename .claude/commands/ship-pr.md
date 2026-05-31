---
name: ship-pr
description: Ships a finished branch through this repo's PR pipeline — open a draft PR, trigger the CodeRabbit review that drafts otherwise skip, address findings critically, then mark ready and squash-merge as `title (#NN)` and hand back the merge link. Use when finishing a branch or shipping/merging a PR, or when the user says "ship it", "merga", "öppna en PR och merga", or asks to run the review→merge cycle.
---

Follow the ship-pr skill from skills/engineering/ship-pr/SKILL.md.

Quick steps:
1. Verify: `python -m pytest tests/ -q` green; optionally smoke-test the app boot.
2. Keep one concern per PR (rebase onto main, drop unrelated commits).
3. Push branch; open a **draft** PR (`mcp__github__create_pull_request`, base main).
4. Trigger review — drafts are skipped: comment `@coderabbitai review`, then wait.
5. Address findings critically (verify, don't blindly apply); push fixes.
6. Mark ready (`draft: false`) → squash-merge as `<title> (#NN)`.
7. Give the user the merge link. Note: `main` auto-deploys via Streamlit Cloud.

---
name: setup-matt-pocock-skills
description: Sets up an `## Agent skills` block in AGENTS.md/CLAUDE.md and `docs/agents/` so the engineering skills know this repo's issue tracker (GitHub or local markdown), triage label vocabulary, and domain doc layout. Run before first use of `to-issues`, `to-prd`, `triage`, `diagnose`, `tdd`, `improve-codebase-architecture`, or `zoom-out`.
disable-model-invocation: true
---

# Setup Matt Pocock's Skills

Scaffold the per-repo configuration that the engineering skills assume:

- **Issue tracker** — where issues live
- **Triage labels** — the strings used for the five canonical triage roles
- **Domain docs** — where `CONTEXT.md` and ADRs live

## Process

1. Explore the repo. Check for existing `CLAUDE.md`/`AGENTS.md`, `docs/agents/`, `CONTEXT.md`, `docs/adr/`.
2. Ask three questions one at a time:
   - **A — Issue tracker**: GitHub Issues, GitLab, local markdown, or other?
   - **B — Triage labels**: default strings or custom mappings?
   - **C — Domain docs**: single-context or multi-context?
3. Edit whichever of `CLAUDE.md` / `AGENTS.md` already exists. Append or update the `## Agent skills` block.
4. Write `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/domain.md`.
5. Tell the user setup is complete.

---
name: to-issues
description: Break a plan, spec, or PRD into independently-grabbable issues on the project issue tracker using tracer-bullet vertical slices. Use when user wants to convert a plan into issues, create implementation tickets, or break down work into issues.
---

# To Issues

Break a plan into independently-grabbable issues using vertical slices (tracer bullets).

The issue tracker and triage label vocabulary should have been provided to you — run `/setup-matt-pocock-skills` if not.

## Process

1. Work from conversation context. Fetch any referenced issue from the tracker.
2. Explore the codebase if needed. Use domain glossary vocabulary.
3. Draft vertical slices — each is a thin complete path through ALL layers (schema, API, UI, tests).
4. Quiz the user on granularity, dependencies, HITL vs AFK classification.
5. Publish approved issues in dependency order.

## Issue template

```markdown
## What to build

A concise description of this vertical slice.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

None - can start immediately.
```

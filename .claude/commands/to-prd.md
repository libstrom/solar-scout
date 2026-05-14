---
name: to-prd
description: Turn the current conversation context into a PRD and publish it to the project issue tracker. Use when user wants to create a PRD from the current context.
---

Take the current conversation context and produce a PRD. Do NOT interview the user — synthesize what you already know.

## Process

1. Explore the repo. Use domain glossary vocabulary throughout.
2. Sketch major modules to build or modify. Look for deep module opportunities.
3. Check with user that modules match expectations.
4. Write and publish the PRD with `ready-for-agent` label.

## PRD template

```markdown
## Problem Statement

## Solution

## User Stories

1. As an <actor>, I want <feature>, so that <benefit>

## Implementation Decisions

## Testing Decisions

## Out of Scope

## Further Notes
```

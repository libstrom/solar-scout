---
name: write-a-skill
description: Create new agent skills with proper structure, progressive disclosure, and bundled resources. Use when user wants to create, write, or build a new skill.
---

# Writing Skills

## Process

1. **Gather requirements** — task/domain, use cases, need for scripts or just instructions.
2. **Draft the skill** — command `.md` file with concise instructions.
3. **Review with user** — coverage, clarity, detail level.

## Structure

For `.claude/commands/`, each skill is a single `.md` file named after the command:

```
.claude/commands/my-skill.md
```

## File template

```md
---
name: skill-name
description: Brief description. Use when [specific triggers].
---

# Skill Name

## Quick start

[Minimal working example]
```

## Description requirements

- Max 1024 chars
- Third person
- First sentence: what it does
- Second sentence: "Use when [specific triggers]"

## Review checklist

- [ ] Description includes "Use when..."
- [ ] File concise (under 100 lines where possible)
- [ ] No time-sensitive info
- [ ] Concrete examples included

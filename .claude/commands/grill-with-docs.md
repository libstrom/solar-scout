---
name: grill-with-docs
description: Grilling session that challenges your plan against the existing domain model, sharpens terminology, and updates documentation (CONTEXT.md, ADRs) inline as decisions crystallise. Use when user wants to stress-test a plan against their project's language and documented decisions.
---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing.

If a question can be answered by exploring the codebase, explore the codebase instead.

## Domain awareness

During codebase exploration, look for existing documentation:

- `CONTEXT.md` at root (or per-context if `CONTEXT-MAP.md` exists)
- `docs/adr/` for architectural decisions

Create files lazily — only when you have something to write.

## During the session

- **Challenge against the glossary**: call out term conflicts with `CONTEXT.md` immediately.
- **Sharpen fuzzy language**: propose precise canonical terms.
- **Cross-reference with code**: surface contradictions between what the user says and what the code does.
- **Update CONTEXT.md inline**: capture resolved terms as they happen.
- **Offer ADRs sparingly**: only when a decision is hard to reverse, surprising without context, and the result of a real trade-off.

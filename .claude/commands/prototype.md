---
name: prototype
description: Build a throwaway prototype to flush out a design before committing to it. Routes between two branches — a runnable terminal app for state/business-logic questions, or several radically different UI variations toggleable from one route. Use when the user wants to prototype, sanity-check a data model or state machine, mock up a UI, explore design options, or says "prototype this", "let me play with it", "try a few designs".
---

# Prototype

A prototype is **throwaway code that answers a question**. The question decides the shape.

## Pick a branch

- **"Does this logic / state model feel right?"** → tiny interactive terminal app pushing the state machine through hard cases.
- **"What should this look like?"** → several radically different UI variations on a single route, switchable via URL search param.

If ambiguous, default to whichever matches the surrounding code and state the assumption.

## Rules

1. **Throwaway from day one, clearly marked.**
2. **One command to run.**
3. **No persistence by default.** State lives in memory.
4. **Skip the polish.** No tests, no error handling beyond runnable, no abstractions.
5. **Surface the state.** Print or render full relevant state after every action.
6. **Delete or absorb when done.**

## When done

Capture the *answer* somewhere durable (commit message, ADR, issue, or `NOTES.md`) along with the question it was answering.

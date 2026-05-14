---
name: improve-codebase-architecture
description: Find deepening opportunities in a codebase, informed by the domain language in CONTEXT.md and the decisions in docs/adr/. Use when the user wants to improve architecture, find refactoring opportunities, consolidate tightly-coupled modules, or make a codebase more testable and AI-navigable.
---

# Improve Codebase Architecture

Surface architectural friction and propose **deepening opportunities** — refactors that turn shallow modules into deep ones.

## Key terms

- **Module** — anything with an interface and an implementation.
- **Depth** — a lot of behaviour behind a small interface. **Deep** = high leverage. **Shallow** = interface nearly as complex as the implementation.
- **Seam** — where an interface lives; a place behaviour can be altered without editing in place.
- **Deletion test**: imagine deleting the module. If complexity reappears across N callers, it was earning its keep.

## Process

### 1. Explore

Read `CONTEXT.md` and any ADRs first. Then walk the codebase and note friction:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules **shallow**?
- Which parts of the codebase are hard to test?

### 2. Present candidates

Numbered list of deepening opportunities. For each:

- **Files** — which files/modules are involved
- **Problem** — why the current architecture causes friction
- **Solution** — plain English description
- **Benefits** — locality and leverage

Ask: "Which of these would you like to explore?"

### 3. Grilling loop

Walk the design tree with the user. Update `CONTEXT.md` inline as terms are resolved. Offer ADRs when a rejected candidate has a load-bearing reason.

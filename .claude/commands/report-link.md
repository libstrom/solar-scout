---
name: report-link
description: Delivers a generated report or file to the user and links it from their Windows Downloads folder (file:///C:/Users/linus/Downloads/<filename>) instead of a raw container path like /root/.claude/... which can't be opened on their machine. Use after any command prints a local file path — /insights HTML, exported CSV/XLSX, diagrams, logs — or whenever the user would otherwise be handed a container-local path.
---

Follow the report-link skill from skills/engineering/report-link/SKILL.md.

Quick steps:
1. Send the file with `SendUserFile` so it reaches the user's machine.
2. Give the link as `file:///C:/Users/linus/Downloads/<basename>` — never the container path.

---
name: ask-matt
description: >
  Use when the user invokes /ask-matt, asks "which skill should I use" or
  "what's the right flow for this", or seems lost about where to start a
  piece of work.
disable-model-invocation: true
---

# Ask Matt (solar-scout edition)

You don't remember every skill, so ask. Read the situation, name the skill,
say why in one line. Adapted from mattpocock/skills — routes to the names
that actually exist in this repo.

## The main flow: idé → shippad

1. **`/grill-with-docs`** — sharpen the idea by interview, stateful
   (writes to `CONTEXT.md` + ADRs). No codebase context needed?
   `/grill-me` (stateless). Just exploring? `/brainstorming`.
2. **Behöver frågan körbar kod för att besvaras?** (state-modell, UI du
   måste se) → `/prototype`. Throwaway: behåll svaret, släng koden.
3. **Fler-sessionsbygge?**
   - Ja → `/to-prd` (tråden blir PRD på issue-trackern), sedan
     `/to-issues` (tracer-bullet-slices med blockers).
   - Nej → bygg direkt: `/writing-plans` om planen är icke-trivial,
     `/executing-plans` eller `/subagent-driven-development` för att
     exekvera den, `/tdd` för varje beteende.
4. **Före merge**: `/verify` (kör flödet på riktigt, inte bara test),
   `/ponytail-review` (överkomplexitet), `/security-review` vid behov.
5. **`/ship-pr`** — draft-PR → CodeRabbit → åtgärda → squash-merge.
   `main` auto-deployar via Streamlit Cloud.

Håll steg 1–3 i ett obrutet kontextfönster; varje implementering därefter
startar fräscht från sin issue.

## On-ramps

- **Bug/önskemål strömmar in** → `/triage` (bara för issues du inte
  själv skapade — `/to-issues`-output är redan agent-redo).
- **Något är trasigt** → `/diagnose` (hårda buggar: reproducera →
  minimera → instrumentera) eller `/systematic-debugging` innan du
  föreslår någon fix alls.
- **Arkitekturen bromsar** → `/improve-codebase-architecture` — hittar
  kandidater; valet blir en idé som går in i huvudflödet vid steg 1.

## Domän: solar-scout

- **David behöver leads NU** → `/leads-now` — beslutsvägen för stad,
  0-leads-checkar, export.
- **Scan ger 0 leads / kraschar** → `/scan-debug` — pipelinen steg för
  steg (Overpass → OSM → Anthropic → bildkällor).
- **Se appen köra / screenshot** → `/run-solar-scout`.
- **Beroenden inför deploy** → `/pin-deps`.

## Lägen (styr hur, inte vad)

- **`/ponytail [lite|full|ultra]`** — minsta kod som funkar. Persistent.
- **`/caveman`** — minsta prosa. Persistent. Paras med ponytail.

## Skills om skills

- Ny skill → `/write-a-skill` (struktur) + `/writing-skills` (kvalitet).
- Förbättra befintlig med eval.json → `/improve-skill` (human-i-loopen)
  eller `/auto-research` (autonom loop).

## Underhåll av den här routern

Lägger du till, döper om eller tar bort en användarnådd skill →
uppdatera den här filen. En router som pekar på skills som inte finns
(eller missar nya) är en router som ljuger — det var så `/ask-matt`
dog första gången.

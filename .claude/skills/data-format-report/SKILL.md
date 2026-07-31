---
name: data-format-report
description: >-
  Takes jira-data-fetch's structured JSON and produces a 1-page slide (.pptx)
  answering the five standard status-report questions (what the team is
  doing, impact, efficiency, improvement, risks). The calling Claude
  instance writes the narrative itself from the fetched JSON — no second LLM
  call — then renders it (format_pptx.py). Use after jira-data-fetch, as part
  of the manager subagent's report-building flow.
---

# Data format report

One manual step, one script:

1. **You write `narrative.json`** — see schema below, reading `data.json` from
   jira-data-fetch. This is the step a script can't do; it's why this is a skill
   instead of a fixed template.
2. **`scripts/format_pptx.py`** — merges `data.json` + `narrative.json` into
   the final native, editable single-slide `.pptx` (real text boxes/shapes). No
   arguments: it reads both JSON files from the repo root and writes
   `exec_summary.pptx` there.

```bash
# Any interpreter with requirements.txt installed; the venv's is the safe bet
# (bare `python` isn't on PATH here).
# ... you write narrative.json first, reading data.json ...
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py
```

## `narrative.json` schema

```json
{
  "key_updates": ["5-7 VERB-FIRST past-tense milestone bullets, one line each (~8-14 words) — what got done"],
  "focus_areas": ["3-4 NOUN-PHRASE bullets (no leading verb) — standing workstreams the team is investing attention in; up to ~2 lines each; reserve one slot for a material risk when something is flagged/blocking"],
  "whats_next": ["4-6 bullets, verb-first OR noun phrase — upcoming deliverables and scheduled events; specific dates must be a literal [date] placeholder"]
}
```

**Grounding rules that keep this honest:**

- **All three panels follow a house style set by the manager who presents this
  slide.** The canonical version, with their hand-written examples for each
  panel, is in `.claude/agents/manager.md` → "House style for the three
  narrative panels". **Each panel has a different grammar, and that's
  load-bearing:**

  | Panel | Grammar | Count | Answers |
  |---|---|---|---|
  | `key_updates` | verb-first, past tense | 5-7, one line each | what got done |
  | `focus_areas` | noun phrase, no leading verb | 3-4, ≤2 lines each | where attention is going |
  | `whats_next` | either | 4-6 | what's coming |

  **None of the three carries a `PART-###` key or a percentage.** Keys survive
  only in the auto-rendered Active-epics panel; numbers live in the KPI tiles.
  Condensed examples:

  > `key_updates`  — Created new Partner Integration repo (dhc-pa-adapter).
  > `focus_areas`  — SAFe readiness and transition.
  > `whats_next`   — ARB review on [date].

- **`key_updates`** — verb first, subject dropped, artifact named
  (`dhc-pa-adapter`, Swagger, ARB). Enablement and process milestones (training
  done, sprint cadence started) count, and work outside the `AA-431` initiative
  still earns a line even though it stays out of the delivery numbers.
- **`focus_areas` is investment themes, NOT the risk list** — this changed;
  don't write it as a problem inventory. Noun phrases naming standing
  workstreams ("SAFe readiness and transition."), which may carry a " - why it
  matters" tail. A leading verb means you've written a key update in the wrong
  box. Because a focus area asserts no completion, it may be forward-leaning —
  but the theme must trace to real tickets/epics in `data.json`.
  **Reserve one slot for a material risk** when `data.flagged` is non-empty or an
  unstarted epic is gating others, in the same noun-phrase voice with the
  consequence named ("Partner Auth & Access dependency — blocks FormPick, Drugs,
  and PA Initiation."). This is the only place risk detail reaches the slide, so
  skipping it hides bad news. A count is allowed here when it *is* the point.
- **`whats_next`** — upcoming deliverables and scheduled events; name the
  external audience when there is one ("to Priorly in customer meeting").
  **`data.json` contains no due dates on any issue**, so every specific date is
  external knowledge: name the milestone and write a literal `[date]` for the
  presenter to fill, never a guessed `8/4`. The *event* must still be groundable
  even when the date isn't. Open-ended horizons need no placeholder ("by end of
  calendar year", "next sprint", "once ARB clears"). Overlap with `focus_areas`
  is expected — a topic can be both a standing theme and a concrete next step;
  don't dedupe. Omit the key to omit the panel.
- **The verb must match the item's real status — that's what keeps it honest.**
  Created / Finalized / Completed / Submitted = done, and must trace to
  `data.resolved_this_period`. Initiated / Built out / Started = real and
  underway, sourced from an in-flight epic or story. Choose the accurate verb
  and then stop; do **not** append a hedge ("…, foundation for future work, not
  user-facing yet") to prop up an overstated one. Two specific traps, both real
  failures on this project:
  - **Aggrandizing verbs.** A ticket to "create a repo / folder scaffold" is
    *"Created new Partner Integration repo"* — NOT "stood up the service" or
    "delivered a working codebase." A *spike* "researched X" or "proposed
    high-level options" — NOT "settled the design" or "removed a core unknown."
    A spike explicitly hedged ("preemptively," "in the event that we need to")
    did not decide anything, so it can't take a "Finalized" verb.
  - **Importing epic scope onto a small ticket.** Do not attach the epic's
    ambition ("the service that will host *every* partner-facing API endpoint")
    to a task that merely created an empty repo. State what the item did; let
    the epic panel carry the ambition.
  Test each bullet: *would the engineer who did this work recognize it in this
  sentence, or would they say "that's not what I did"?* If the latter, cut the
  claim back or downgrade the verb.

## Five-question → field mapping

| # | Question | Field(s) | Source data |
|---|---|---|---|
| 1 | What is the team doing | epic names + descriptions in `data.recent_epics` | `recent_epics.linked`/`.other` (each epic carries `description`) |
| 2 | What is the impact | `key_updates` | `resolved_this_period` (each has `description`) for completed-verb bullets; in-flight epics/stories for "initiated"-verb ones — NOT epic goals |
| 3 | Is work efficient | auto-rendered Epic cycle time / Throughput-per-week / Story-pts-completed tiles | `epic_cycle_time` (`{days, resolved_epics}` — epic-level, not per-ticket; check `resolved_epics` before trusting a thin number), `throughput_per_week`, `total_completed_points`, `sprint_goal` (null until a sprint is active — don't substitute a proxy silently when it is) |
| 4 | How is the team improving | fold into `key_updates`/`focus_areas` — no dedicated panel | **No trend data exists** — `data.json` is a single-window snapshot with no baseline. Answer this from concrete process facts you can point at (a dependency unblocked, a spec landing that unblocks N stories, a flag cleared), never from a metric direction |
| 5 | What's going wrong/slow | **the one reserved risk slot in `focus_areas`** + the auto-rendered Flagged KPI tile (a count only — no detail panel) | `data.flagged`/`stale`/`overdue` (`flagged` is Jira's native Flag/Impediment field, not an issue-link relationship, and its items key off `issue`, not `key`). `focus_areas` is themes now, so the worst item by `days_since_update`/`days_overdue` gets **one** noun-phrase bullet with the consequence named — that's the whole answer to Q5 on the slide. If all three are empty, say "nothing flagged/stale/overdue" to the caller and spend the slot on a theme |

## Guidance for writing good bullets

See CLAUDE.md's "Narrative-writing rules" and "Project-specific data quirks"
sections for the rules shared with `manager.md` (no `priority` field on
epics/use Rank, the blank Project-health tile, never claiming a metric
direction, `recent_epics.other` reported separately, the per-panel bullet
counts). The rest, specific to writing bullets for this slide:

- **Every claim traces to a specific item in `data.json`** — you must be able to
  name the ticket or epic behind each bullet even when you don't print its key.
- **Name the consequence instead of the count.** The old advice here was to
  quantify in `focus_areas` ("5 of 7 linked epics have zero child issues"); the
  house style drops bare metrics from all three panels, so carry the *stakes* in
  words instead — "blocks FormPick, Drugs, and PA Initiation" tells a skip-level
  reader what to do about it, and the KPI tiles supply the arithmetic.
- **Distinguish real signal from data-hygiene noise.** Check `data.auto_caveats`
  before featuring a number as insight (e.g. don't report "60% unassigned" as
  a workload finding if it's flagged as untriaged noise).
- **`recent_epics.excluded`** are filtered test/junk epics — don't resurrect
  them into the narrative.
- Keep each bullet to one sentence — it becomes a bullet in a slide panel,
  not a paragraph.

## Implementation

See `scripts/format_pptx.py` for rendering. It reads `data.json` +
`narrative.json` from the repo root and produces the native, editable
single-slide `.pptx` (real text boxes/shapes; the slide is the KPI tile row,
then two even columns) for sharing/hand-editing in PowerPoint before
sending on. It is self-contained (only `python-pptx`) — it does not depend on
the Jira engine or any HTML template. Scoped to the fields PART's Jira data
supports: the GitHub PR tile renders only when `pull_requests.configured` is
true (omitted entirely otherwise); the Story-points tile reads
`total_completed_points` (points on resolved issues across every sprint ever
run) and falls back to a "no sprint data yet" placeholder only when that is
`null` — never a mock number. PRs-per-sprint grouping stays in a "(no sprint)"
bucket until the linked tickets carry a sprint.

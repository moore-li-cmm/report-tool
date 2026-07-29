---
name: data-format-report
description: >-
  Takes jira-data-fetch's structured JSON and produces a 1-page slide (.pptx)
  answering the six standard status-report questions (what the team is
  doing, impact, value, efficiency, improvement, risks). The calling Claude
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
   the final native, editable single-slide `.pptx` (real text boxes + a
   native chart). No arguments: it reads both JSON files from the repo root and
   writes `exec_summary.pptx` there.

```bash
# Use the repo's venv interpreter — bare `python` is not on PATH.
# ... you write narrative.json first, reading data.json ...
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py
```

## `narrative.json` schema

```json
{
  "key_updates": ["2-3 SHORT one-line bullets — impact, grounded in what actually RESOLVED (shares the center column with focus_areas — keep tight)"],
  "focus_areas": ["2-3 SHORT one-line bullets — what needs attention NOW (shares the center column with key_updates — keep tight)"],
  "whats_next": ["2-4 short bullets — the near-term deliverables/milestones coming NEXT period; forward-looking, distinct from focus_areas"],
  "extra_caveats": ["optional — anything data-quality-relevant auto_caveats didn't catch"],
  "mission_line": "optional one-liner under the header — what this initiative is for, in plain language",
  "trend_annotation": "optional one-liner shown next to the throughput chart"
}
```

`mission_line` and `trend_annotation` are optional — **do not invent content
for them.** If you don't have a real answer, omit the key (the renderer
simply omits the line) rather than writing plausible-sounding filler.

**Two grounding rules that keep this honest — they are the whole point of
feeding you the description text, not just counts:**

- **Business value goes in the one-line `mission_line`, not a panel.** Derive
  it from `data.initiative_description` and state it once at the top — don't
  write a separate value panel. If the description is empty,
  keep `mission_line` short and factual — never invent value.
- **`whats_next` is the forward-outlook panel in the left slot.** 2-4 bullets
  on the near-term deliverables/milestones coming next, derived from epic
  sequencing/descriptions and the pending decisions in the data. Use honest
  timing you can actually support ("next sprint", "once ARB clears") — do NOT
  invent calendar dates that aren't in Jira. Keep it distinct from
  `focus_areas` (what needs attention *now*). Omit the key to omit the panel.
- **Impact (`key_updates`) comes from what RESOLVED, not from epic goals.**
  Epic descriptions describe what the team is *building toward* — aspirational
  scope. `data.resolved_this_period` (with its own `description` text) is what
  actually shipped. Ground impact bullets in the resolved items. If the
  resolved work is just plumbing/enablement (small tasks, empty descriptions),
  say "foundation work, nothing user-facing yet" — that's an honest answer,
  not a gap to paper over with epic ambition.
- **Never inflate a bullet past what its ticket supports.** Two specific traps,
  both real failures on this project:
  - **Aggrandizing verbs.** A ticket to "create a repo / folder scaffold" is
    *"created the repo and directory scaffold"* — NOT "stood up the service"
    or "delivered a working codebase." A *spike* "researched X" or "proposed
    high-level options" — NOT "settled the design" or "removed a core unknown."
    A spike explicitly hedged ("preemptively," "in the event that we need to")
    must keep that hedge; it did not decide anything.
  - **Importing epic scope onto a small ticket.** Do not attach the epic's
    ambition ("the service that will host *every* partner-facing API endpoint")
    to a task that merely created an empty repo. State what the ticket did; let
    the epic panel and the `mission_line` carry the ambition.
  Test each bullet: *would the engineer who closed this ticket recognize their
  work in this sentence, or would they say "that's not what I did"?* If the
  latter, cut it back to the ticket.

## Six-question → field mapping

| # | Question | Field(s) | Source data |
|---|---|---|---|
| 1 | What is the team doing | `mission_line`, epic names + descriptions in `data.recent_epics` | `recent_epics.linked`/`.other` (each epic carries `description`) |
| 2 | What is the impact | `key_updates` | `resolved_this_period` (each has `description`) — what actually shipped, NOT epic goals |
| 3 | Why is the impact valuable | `mission_line` (one-liner at the top) | From `initiative_description`; stated once up top, not repeated as a panel. The left slot hosts `whats_next` (forward outlook) |
| 4 | Is work efficient | auto-rendered Epic cycle time / Stories completed tiles | `epic_cycle_time` (`{days, prior_days, resolved_epics}` — epic-level, not per-ticket; check `resolved_epics` before trusting a thin number), `throughput_per_week`, `prior_period`, `sprint_goal`/`velocity_history` (null until a sprint is active/closed — don't substitute a proxy silently when they are) |
| 5 | How is the team improving | `trend_annotation` + the cycle-time/throughput deltas | `data.trend` (only claim a direction if ≥2 non-zero weeks in each half of the window — else call it too thin) and the epic-cycle-time/throughput deltas vs `prior_period` |
| 6 | What's going wrong/slow | `focus_areas` + the auto-rendered Flagged KPI tile (a count only — no detail panel) | `data.blocked`/`stale`/`overdue` (surface the worst by `days_since_update`/`days_overdue` in `focus_areas` — the tile shows no detail) — if all three are empty, say "nothing blocked/stale/overdue" explicitly, don't omit the section |

## Guidance for writing good bullets

- **Ground every claim in a number or issue key from `data.json`** — "epics
  are stalled" is weak; "5 of 7 linked epics have zero child issues created"
  is what a skip-level reader needs.
- **Distinguish real signal from data-hygiene noise.** Check `data.auto_caveats`
  before featuring a number as insight (e.g. don't report "60% unassigned" as
  a workload finding if it's flagged as untriaged noise).
- **`recent_epics.other`** don't roll up to the tracked initiative (e.g.
  internal enablement) — call these out separately, don't blend them in.
- **`recent_epics.excluded`** are filtered test/junk epics — don't resurrect
  them into the narrative.
- **"Project health" is a deliberately blank tile — an empty circle with no
  computed value.** It's a manual fill-in for whoever presents the slide to
  color/annotate by hand in PowerPoint; do not write a `delivery_health` key
  or otherwise try to compute a status for it — `format_pptx.py` doesn't read
  one. `data.suggested_status` still exists in `data.json` as a naive
  reference signal (blocker→critical, nothing delivered→warning, else good) if
  you want to mention it in prose, but nothing in the narrative drives the tile.
- **Thin trends are worth flagging, not smoothing over** — if `data.trend`
  has fewer than two non-zero weeks in each half, say the window is too
  short/thin rather than implying a steady pace that isn't there.
- Keep each bullet to one sentence — it becomes a bullet in a slide panel,
  not a paragraph.
- **`key_updates` and `focus_areas` render stacked in the same center column**,
  so keep each to ~3 short one-line bullets. The pptx shrinks text to fit if a
  panel runs long, but tight bullets keep the type readable — don't rely on the
  shrink to rescue a wall of text.

## Implementation

See `scripts/format_pptx.py` for rendering. It reads `data.json` +
`narrative.json` from the repo root and
produces the native, editable single-slide `.pptx` (real text boxes + a native
chart) for sharing/hand-editing in PowerPoint before sending on. It is
self-contained (only `python-pptx`) — it does not depend on the Jira engine or
any HTML template. Scoped to the fields PART's Jira data supports: GitHub PR
activity renders when configured; the Story-points KPI tile and the velocity
chart render real numbers once `sprint_goal`/`velocity_history` are non-null
(an active/closed sprint exists), and fall back to a "not tracked"/"awaiting
data" placeholder otherwise — never a mock number. PRs-per-sprint grouping is
similarly dormant until a sprint exists.

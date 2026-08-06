# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pipeline that turns live Jira data (project `PART`, initiative `AA-431`) into a
single-page, editable executive-summary slide (`exec_summary.pptx`). There is no
app to run — the "product" is a `manager` subagent + two Claude Code skills that
fetch data, write a narrative, and render a slide.

## Commands

```bash
# Full report (what a user actually invokes):
#   /weekly-report   →  runs the manager subagent end-to-end

# Manual pipeline (use ./.venv/bin/python — see the interpreter note below):
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py          # writes data.json
# ... write narrative.json by hand per data-format-report/SKILL.md ...
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py # writes exec_summary.pptx

# Deps — .venv is gitignored, so create it on a fresh clone:
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt   # requests, python-dotenv, python-pptx
```

No test suite, linter, or build step exists in this repo.

**Interpreter:** the only real requirement is Python 3.9+ with
`requirements.txt` installed — no code reads a `.venv`, so any interpreter
carrying those three deps runs the scripts. Prefer `./.venv/bin/python`
anyway: bare `python` doesn't exist here, and a system `python3` may or may not
have the deps, so the venv is the one path guaranteed to work.

Neither script takes arguments — project (`PART`), the 30-day window, and file
paths are fixed constants at the top of `fetch.py`. Both scripts read/write
`data.json`, `narrative.json`, `exec_summary.pptx` at the **repo root**,
regardless of cwd (they resolve `_REPO_ROOT` from `__file__`).

Credentials live in repo-root `.env` (`JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`; optionally `GITHUB_TOKEN`/`GITHUB_REPOS`/`GITHUB_API_URL` for
PR metrics) — never commit real values there.

## Architecture

Three-stage pipeline, each stage a separate Claude Code concept:

1. **`jira-data-fetch` skill** (`.claude/skills/jira-data-fetch/scripts/`) — pure
   retrieval + aggregation, no prose. `fetch.py` is a thin orchestrator over three
   library modules that sit alongside it (not a package, just sibling files that
   import each other by bare name):
   - `jira_report.py` — the Jira Cloud data-access layer: auth (`verify_auth`,
     which explicitly probes `/myself` because Atlassian's search endpoint
     silently returns 200 + empty results for a bad token), `fetch_issues`
     (paginated JQL search), `fetch_changelog`, and `adf_to_text` (flattens
     Jira's rich-text ADF JSON to plain text — this is what lets downstream
     narrative-writing use real description text instead of just ticket titles).
   - `jira_exec_summary.py` — the actual stats engine (`compute_stats`):
     initiative-scoped backlog, delivery/throughput, epic cycle time (creation→
     resolution over resolved epics), epic rollup to
     initiative `AA-431` with per-epic Rank/status/new/done-recent/rank-change
     (via changelog), flagged tickets (Jira's native Flag/Impediment field,
     not issue links), data-hygiene `auto_caveats`.
   - `github_prs.py` — optional GitHub PR stats (opened/merged/open, cross-linked
     to Jira keys parsed from PR title/body). Degrades to `configured: false`
     rather than failing the run if unset or erroring.
   - `fetch.py` adds two things the engine doesn't: `active_issues` and
     `overdue`, then aliases fields to match the SKILL.md output contract.
     `sprint_goal`/`total_completed_points` are computed live by
     `jira_exec_summary.compute_sprint_stats` from the Sprint/Story-Points
     fields — null only while there's genuinely no active sprint / no sprint
     data at all (see below), not hardcoded.
   - Output: `data.json` at repo root. Schema documented in
     `.claude/skills/jira-data-fetch/SKILL.md`.

2. **`manager` subagent** (`.claude/agents/manager.md`) — the analysis step, done
   by an LLM reading `data.json`, not a script. Writes `narrative.json` by hand
   following the five-question → field mapping in
   `.claude/skills/data-format-report/SKILL.md` (what the team's doing / impact /
   efficiency / improvement / risks). This is where all the
   "don't inflate a ticket past what it actually did" judgment calls happen.

3. **`data-format-report` skill** (`.claude/skills/data-format-report/scripts/format_pptx.py`)
   — pure rendering. Merges `data.json` + `narrative.json` into a native,
   editable single-slide `.pptx` (real shapes/text boxes via `python-pptx`) —
   self-contained, no dependency on the Jira engine.

The `/weekly-report` slash command (`.claude/commands/weekly-report.md`) is the
user-facing entry point; it just invokes the `manager` subagent.

## Project-specific data quirks (baked into the engine — don't "fix" them)

- Epics roll up to initiative `AA-431` via `INITIATIVE_KEY` in
  `jira_exec_summary.py`; epics not under it land in `recent_epics.other` and
  must be reported separately, not blended into initiative progress.
- `PART-1` ("Sample Epic") is test data (status Discard), excluded from epic
  rollups upstream via `EXCLUDED_STATUSES` — never resurface it. Status
  `Discard` also marks other one-off test/junk tickets project-wide (not just
  PART-1's children — e.g. standalone "delete me" stories under real epics);
  `_WORKITEM_FILTER` excludes all of them from `backlog_total`/
  `resolved_this_period`/`backlog_delivered`/`throughput_per_week`, with a
  caveat naming which tickets were dropped so the exclusion isn't silent.
- **Sprint field (`customfield_10020`) and Story Points field
  (`customfield_13078`) are now populated** — a sprint ("PartInt Pilot 1")
  started 2026-07-29. `sprint_goal`/`total_completed_points` are computed live
  from these (`jira_exec_summary.compute_sprint_stats`) and stay `null` only
  when there's genuinely no active sprint (`sprint_goal`) or the Sprint field
  has never been populated at all (`total_completed_points`) — `auto_caveats`
  states which case applies (e.g. "sprint active but no goal set", "Story Points
  aren't estimated on any ticket yet"). Report
  `throughput_per_week`/`epic_cycle_time` instead whenever these are null;
  never fabricate a story-point number. No code change is needed as the sprint
  moves through states.
- **Backlog is initiative-scoped**: `backlog_total` counts only open tickets
  whose parent epic rolls up to a real Initiative; orphan tickets are excluded
  (a caveat reports how many). It intentionally won't match the board's raw
  open count. `backlog_delivered`/`throughput_per_week`/`resolved_this_period`
  are scoped the same way — a
  resolved item only counts if its parent epic rolls up to a real Initiative,
  with a caveat naming how many resolved tickets were excluded. This can
  legitimately read `0` even when real work shipped that period, if the
  shipped work landed under a non-initiative epic or an orphan ticket — that's
  the scope working as intended, not a bug; the manager should say so plainly
  rather than reporting "nothing delivered."
- **Cycle time is epic-level** (`epic_cycle_time.days`), not per ticket, and is
  scoped to initiative-connected epics only — same scoping as
  `backlog_delivered`, so an epic that doesn't roll up to a real initiative
  can't drive this number. `null` when no *initiative* epic resolved in the
  window — treat as n/a; with a low `resolved_epics` count one epic swings it.
  An `auto_caveats` line names any non-initiative epic resolved in-window that
  was excluded for this reason.
- **Active Epics panel** renders `recent_epics.linked` in **real backlog-Rank
  order** (engine pre-sorts by Jira's native `Rank` field, `customfield_10019`
  on this instance — the team's actual drag-and-drop order), shown as an
  ordinal `#1`/`#2`/… badge per row, each row also showing child progress
  (`done/total`, or `N/A` for a childless epic) plus **NEW** (created in-window)
  and rank-change badges. The row does **not** print the epic's workflow status:
  `in_flight` (Jira "In Progress" category) is computed into `data.json` and is
  available to the manager in prose, but there's no horizontal room for it on the
  row. Epics carry **no `priority` field at all** — PART's Priority sits at
  an unused default ("Lowest") on every epic and carries no signal, which is
  why ranking/ordering is derived from Rank instead. `rank_change` comes from
  changelog but only ever carries a bare direction (`raised`/`lowered`) — Jira
  logs Rank moves as "Ranked higher"/"Ranked lower" with no absolute from/to
  position. Each epic also carries `is_new`/`is_done_recent` (created/resolved
  within the reporting window), which feed the slide's "Epics created |
  completed" KPI tile — both halves time-boxed to the reporting window.
- **Finished epics age out of the panel after one appearance.** An epic
  resolved *before* the window goes to `recent_epics.aged_out` instead of
  `linked`/`other`, so it's shown in the window it completes (where
  `is_done_recent` is true) and gone the next — otherwise done work holds a
  Rank slot in the top-6 forever. The filter runs *after*
  `initiative_epic_keys` is built, deliberately: backlog/throughput/cycle-time
  scoping must still see aged-out epics or resolved tickets under them would
  silently stop counting as delivery. A Done epic with no `resolutiondate`
  can't be dated, so it stays visible. An `auto_caveats` line names what aged
  out, so the drop isn't silent.
- `initiative_status` (AA-431's own Jira status/phase) is still computed and
  present in `data.json`, but has no dedicated slide tile. It's still available
  for the manager to reference in prose if useful.
- **"Project health" is a deliberately blank KPI tile** — an empty, uncolored
  circle for whoever presents the slide to annotate by hand in PowerPoint. It
  is never auto-computed from `data.json` or written by the manager into
  `narrative.json`; `format_pptx.py` doesn't read a status value for it at all.
  `suggested_status` (`{level, label}`: flagged→critical, nothing
  delivered→warning, else good) still exists in `data.json` as a naive
  reference signal the manager may mention in prose, but it drives no tile.
- Retargeting this at a different Jira project means updating: `PROJECT` in
  `fetch.py`; `INITIATIVE_KEY`/`INITIATIVE_NAME`/`EXCLUDED_STATUSES` in
  `jira_exec_summary.py`; `SPRINT_FIELD_ID` in `jira_report.py`;
  `STORY_POINTS_FIELD_ID`/`RANK_FIELD_ID` in `jira_exec_summary.py` (custom
  field IDs are per-instance — re-derive via `GET /rest/api/3/field`; Rank is
  usually named exactly "Rank", type `gh-lexo-rank`).

## Narrative-writing rules (apply when editing `manager.md` or writing `narrative.json`)

- **All three narrative panels follow a house style set by the manager who
  presents the slide**, and each has a *different grammar* — that distinction is
  load-bearing, so don't collapse it. The canonical version with their
  hand-written examples lives in `.claude/agents/manager.md` ("House style for
  the three narrative panels"); `data-format-report/SKILL.md` carries the
  condensed table.

  | Panel | Grammar | Count |
  |---|---|---|
  | `key_updates` | verb-first past tense — "Created new Partner Integration repo (dhc-pa-adapter)." | 5-7, one line each |
  | `focus_areas` | noun phrase, no leading verb — "SAFe readiness and transition." | 3-4, ≤2 lines each |
  | `whats_next` | either, plus a date when there is one — "ARB review on [date]." | 4-6 |

  **No `PART-###` keys and no percentages in any of the three.** Keys survive only
  in the auto-rendered Active-epics panel; numbers live in the KPI tiles. A
  leading verb in `focus_areas` is the most common slip — it means a key update
  got written in the wrong box.
- `whats_next` (forward outlook) is the only "upcoming" content — no separate
  value panel exists. It's also where scheduled events and external audiences go
  ("Present … to Priorly in customer meeting on [date]").
- **`focus_areas` is investment themes, not a risk list** (changed from the
  original design). It names standing workstreams, optionally with a " - why it
  matters" tail. Because a noun phrase claims no completion, a focus area may be
  forward-leaning — but it must trace to real tickets/epics. **One slot is
  reserved for a material risk** whenever `data.flagged` is non-empty or an
  unstarted epic gates others, phrased as a noun phrase naming the consequence
  ("Partner Auth & Access dependency — blocks FormPick, Drugs, and PA
  Initiation."). That slot is the *only* place risk detail reaches the slide —
  the Flagged KPI tile is a bare count with no detail panel — so dropping it
  hides bad news.
- **Never invent a date.** `data.json` carries no due dates on any issue, so any
  specific date is external knowledge: name the milestone and leave a literal
  `[date]` for the presenter. The *event* must still be groundable even when the
  date isn't. Open-ended horizons ("by end of calendar year", "next sprint",
  "once ARB clears") need no placeholder.
- **Never lift a proper noun the data can't source — but search for the concept,
  not the manager's exact spelling, before calling it ungroundable.** Jira often
  carries the same workstream under another name: "Evaluation Gate" is **"Eval
  Gate"** there, and **"CMI" is "CoverMyIncident"** — both real and reportable.
  `PDK` and `Dev Gate` genuinely have no ticket, so describe those generically
  ("the agentic development workflow", "ARB review"). Try the acronym expanded,
  the phrase abbreviated, and the obvious synonym first.
- Enablement/process milestones count as `key_updates`, and non-initiative work
  earns a line even though it stays out of the delivery numbers.
- **The verb encodes completion state, and that's what keeps the bullet honest.**
  Created/Finalized/Completed = done, and must trace to `resolved_this_period`;
  Initiated/Built out = real but underway, from an in-flight epic or story.
  Never an epic's aspirational description on its own. Pick the accurate verb
  and stop — don't prop up an overstated one with a trailing hedge.
- Never inflate a bullet past its ticket: a scaffold-only ticket is "Created new
  Partner Integration repo," not "stood up the service"; a spike "researched
  options," not "settled the design." Test: would the engineer who did it
  recognize the claim as their work?
- Never claim a metric direction (throughput/cycle time up or down, faster or
  slower, holding steady). `data.json` is a single-window snapshot with no
  baseline or history — there is nothing to compare against. Answer "how is the
  team improving" from concrete process facts instead, and flag numbers too thin
  to lean on (`resolved_epics` 0-1, `backlog_delivered` 0).
- **Panel geometry, which is what sets the bullet counts.** `key_updates` and
  `focus_areas` share the right column; `whats_next` sits under the epic panel on
  the left. Every box is fixed-height with shrink-to-fit, so overlong content
  gets scaled down small rather than overflowing. Budgets: Key updates 2.4"
  (7 one-liners ≈ 1.35", comfortable), Focus areas and What's next 2.0" each
  (4 two-line bullets ≈ 1.5", and What's next ends at 7.41" on a 7.5" slide, so
  it's the tightest of the three). `key_updates` must stay true one-liners;
  `focus_areas`/`whats_next` may wrap to two. Re-render and check before
  raising any count.

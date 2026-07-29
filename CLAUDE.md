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

# Manual pipeline (venv interpreter required — bare python/python3 is not on PATH):
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py          # writes data.json
# ... write narrative.json by hand per data-format-report/SKILL.md ...
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py # writes exec_summary.pptx

# Deps
pip install -r requirements.txt   # requests, python-dotenv, python-pptx
```

No test suite, linter, or build step exists in this repo.

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
     resolution over resolved epics, with a prior-period delta), epic rollup to
     initiative `AA-431` with per-epic Rank/status/new/done-recent/rank-change
     (via changelog), blockers (via issue links), 8-week resolution trend,
     data-hygiene `auto_caveats`.
   - `github_prs.py` — optional GitHub PR stats (opened/merged/open, cross-linked
     to Jira keys parsed from PR title/body). Degrades to `configured: false`
     rather than failing the run if unset or erroring.
   - `fetch.py` adds two things the engine doesn't: `active_issues` and
     `overdue`, then aliases fields to match the SKILL.md output contract.
     `sprint_goal`/`velocity_history` are computed live by
     `jira_exec_summary.compute_sprint_stats` from the Sprint/Story-Points
     fields — null only while there's genuinely no active/closed sprint (see
     below), not hardcoded.
   - Output: `data.json` at repo root. Schema documented in
     `.claude/skills/jira-data-fetch/SKILL.md`.

2. **`manager` subagent** (`.claude/agents/manager.md`) — the analysis step, done
   by an LLM reading `data.json`, not a script. Writes `narrative.json` by hand
   following the six-question → field mapping in
   `.claude/skills/data-format-report/SKILL.md` (what the team's doing / impact /
   value / efficiency / improvement / risks). This is where all the
   "don't inflate a ticket past what it actually did" judgment calls happen.

3. **`data-format-report` skill** (`.claude/skills/data-format-report/scripts/format_pptx.py`)
   — pure rendering. Merges `data.json` + `narrative.json` into a native,
   editable single-slide `.pptx` (real shapes/text boxes/a native chart via
   `python-pptx`) — self-contained, no dependency on the Jira engine.

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
  started 2026-07-29. `sprint_goal`/`velocity_history` are computed live from
  these (`jira_exec_summary.compute_sprint_stats`) and stay `null` only when
  there's genuinely no active sprint (`sprint_goal`) or no *closed* sprint yet
  (`velocity_history`) — `auto_caveats` states which case applies (e.g. "sprint
  active but no goal set", "no completed sprint yet"). Report
  `throughput_per_week`/`epic_cycle_time` instead whenever these are null;
  never fabricate a velocity number. No code change is needed as the sprint
  moves through states — this was previously hardcoded to `null`
  unconditionally, which is why it looked frozen even after a sprint existed.
- **Backlog is initiative-scoped**: `backlog_total` counts only open tickets
  whose parent epic rolls up to a real Initiative; orphan tickets are excluded
  (a caveat reports how many). It intentionally won't match the board's raw
  open count. `backlog_delivered`/`throughput_per_week`/`resolved_this_period`
  (and `prior_period`, for a like-for-like delta) are scoped the same way — a
  resolved item only counts if its parent epic rolls up to a real Initiative,
  with a caveat naming how many resolved tickets were excluded. This can
  legitimately read `0` even when real work shipped that period, if the
  shipped work landed under a non-initiative epic or an orphan ticket — that's
  the scope working as intended, not a bug; the manager should say so plainly
  rather than reporting "nothing delivered."
- **Cycle time is epic-level** (`epic_cycle_time.days`), not per ticket. `null`
  when no epic resolved in the window — treat as n/a; with a low `resolved_epics`
  count one epic swings it.
- **Active Epics panel** renders `recent_epics.linked` in **real backlog-Rank
  order** (engine pre-sorts by Jira's native `Rank` field, `customfield_10019`
  on this instance — the team's actual drag-and-drop order), shown as an
  ordinal `#1`/`#2`/… badge per row, each row also showing an in-flight status
  (Jira "In Progress" category, rendered as "In Progress" not the raw status
  name) with child progress, plus **NEW** (created in-window) and rank-change
  badges. Epics carry **no `priority` field at all** — PART's Priority sits at
  an unused default ("Lowest") on every epic and carries no signal, which is
  why ranking/ordering is derived from Rank instead. `rank_change` comes from
  changelog but only ever carries a bare direction (`raised`/`lowered`) — Jira
  logs Rank moves as "Ranked higher"/"Ranked lower" with no absolute from/to
  position. Each epic also carries `is_new`/`is_done_recent` (created/resolved
  within the reporting window), which feed the slide's "Started epics | done
  (last Nd)" KPI tile.
- `initiative_status` (AA-431's own Jira status/phase) is still computed and
  present in `data.json`, but has no dedicated slide tile. It's still available
  for the manager to reference in prose (e.g. `mission_line`) if useful.
- **"Project health" is a deliberately blank KPI tile** — an empty, uncolored
  circle for whoever presents the slide to annotate by hand in PowerPoint. It
  is never auto-computed from `data.json` or written by the manager into
  `narrative.json`; `format_pptx.py` doesn't read a status value for it at all.
  `suggested_status` (`{class, label}`: blocker→critical, nothing
  delivered→warning, else good) still exists in `data.json` as a naive
  reference signal the manager may mention in prose, but it drives no tile.
- Retargeting this at a different Jira project means updating: `PROJECT` in
  `fetch.py`; `INITIATIVE_KEY`/`INITIATIVE_NAME`/`EXCLUDED_STATUSES` in
  `jira_exec_summary.py`; `SPRINT_FIELD_ID` in `jira_report.py`;
  `STORY_POINTS_FIELD_ID`/`RANK_FIELD_ID` in `jira_exec_summary.py` (custom
  field IDs are per-instance — re-derive via `GET /rest/api/3/field`; Rank is
  usually named exactly "Rank", type `gh-lexo-rank`).

## Narrative-writing rules (apply when editing `manager.md` or writing `narrative.json`)

- `mission_line` (business value) and `whats_next` (forward outlook) are the
  only "value"/"upcoming" content — no separate value panel exists.
- `key_updates` (impact) must be grounded in `resolved_this_period` (what
  actually shipped), never in aspirational epic descriptions.
- Never inflate a bullet past its ticket: a scaffold-only ticket is "created the
  repo scaffold," not "stood up the service"; a spike "researched options," not
  "settled the design." Test: would the engineer who closed it recognize the
  claim as their work?
- Only claim a trend direction (throughput up/down) with ≥2 non-zero weeks in
  each half of the window — otherwise say the window is too thin.
- `key_updates`/`focus_areas` share one slide column — keep each to ~3 short
  one-line bullets.

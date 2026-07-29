---
name: jira-data-fetch
description: >-
  Fetches structured Jira data for status reporting on the PART project —
  active issues, epic/initiative rollup, throughput trend, cycle time,
  blocked/stale/overdue issues. Wraps the project's existing Jira API layer
  (jira_report.py, jira_exec_summary.py) rather than reimplementing it. Use
  as the first step of building a status report, alongside data-format-report
  and the manager subagent.
---

# Jira data fetch

Produces one JSON file — the factual, ungrouped-by-narrative snapshot that
`data-format-report` and the `manager` subagent build on. No analysis or
prose happens here; this step is pure retrieval + aggregation.

Run from the repo root (where `.env` lives) with the repo's venv interpreter
(bare `python` is not on PATH):

```bash
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py
```

No arguments — the project (PART), the 30-day reporting window, and the 8-week
throughput trend are fixed constants at the top of `fetch.py`, and it writes
`data.json` to the repo root. The engine modules it wraps (`jira_report.py`,
`jira_exec_summary.py`, `github_prs.py`) sit alongside it in `scripts/`.

## Output contract

```json
{
  "project": "PART",
  "period_label": "Last 30 days",
  "generated_at": "...",
  "sprint_goal": {"name": "PartInt Pilot 1", "goal": null, "start_date": "...", "end_date": "...", "committed_points": 26.0, "completed_points": 0, "in_progress_points": 8.0} ,
  "initiative_description": "plain-text 'what/why' pulled from the initiative (AA-431) — the source for business value",
  "initiative_status": "New",
  "active_issues": [{"key": "...", "summary": "...", "status": "...", "issuetype": "...", "assignee": "..."}],
  "recent_epics": {"linked": [{"key": "...", "summary": "...", "done": 0, "total": 0, "description": "epic goal text", "status": "Analyzing", "in_flight": true, "is_new": false, "is_done_recent": false, "rank_change": {"when": "2026-07-20", "direction": "raised"}}], "other": [...], "excluded": [...]},
  "velocity_history": {"sprint_labels": ["Sprint 3", "Sprint 4"], "committed": [20, 24], "completed": [18, 22]},
  "epic_cycle_time": {"days": 14.0, "prior_days": 31.0, "resolved_epics": 2},
  "throughput_per_week": 1.8,
  "prior_period": {"backlog_delivered": 5, "throughput_per_week": 1.2},
  "trend": {"week_labels": [...], "counts": [...]},
  "resolved_this_period": [{"key": "...", "summary": "...", "issuetype": "...", "description": "what actually shipped"}],
  "blocked": [{"issue": "...", "blocked_by": "...", "blocked_by_status": "..."}],
  "stale": [{"key": "...", "summary": "...", "days_since_update": 21}],
  "overdue": [{"key": "...", "summary": "...", "duedate": "...", "days_overdue": 5}],
  "pull_requests": {"configured": true, "opened_in_window": 6, "merged_in_window": 3, "open_now": 2,
                    "current_sprint": null, "by_sprint": {"(no sprint)": {"opened": 6, "merged": 3, "prs": [...]}},
                    "prs": [{"number": 4, "merged": true, "linked_issues": ["PART-127"], "url": "...", "author": "..."}]},
  "auto_caveats": ["..."],
  "suggested_status": {"class": "dot--good|dot--warning|dot--critical", "label": "..."}
}
```

## Description text — the source for "what" and "why"

`initiative_description`, each epic's `description`, and each
`resolved_this_period` item's `description` are plain-text flattenings of the
Jira rich-text fields. They're what lets the manager answer *what the team is
doing* and *why it's valuable* from real source text instead of guessing.
**Value must be synthesized from these; impact must be grounded in the
resolved-item descriptions (what shipped), not epic goals (aspirational).**
When a description is empty (some issues have blank ones), say "Not documented
in JIRA" — never invent to fill the gap.

## Backlog scope, `epic_cycle_time`, and `prior_period`

- **`backlog_total` counts only initiative-connected tickets.** An open work
  item is in the backlog only if its parent epic rolls up to a real Initiative
  (any Initiative, not just AA-431). Loose/orphan tickets — no epic, or an epic
  outside every initiative — are excluded, and an `auto_caveats` line reports
  how many were dropped so the number stays reconcilable against the board.
  `stale`, `priority_breakdown`, and `assignee_breakdown` are computed over this
  same initiative-connected backlog.
- **`backlog_delivered`/`throughput_per_week`/`resolved_this_period` are scoped
  the same way**: a resolved item only counts if its parent epic rolls up to a
  real Initiative, so "delivered" reconciles with "backlog" instead of counting
  a wider whole-project set. `prior_period` uses the identical scoping so the
  trend delta compares like with like. An `auto_caveats` line reports how many
  resolved tickets were excluded for this reason. On a project where recent
  work happens to land under non-initiative epics or orphan tickets, this can
  legitimately show `0` even when real work shipped that period — that's the
  scope working as intended, not a bug; say so explicitly rather than treating
  it as "nothing happened."
- **`epic_cycle_time` is the headline cycle-time metric — measured on EPICS, not
  work items.** `{days, prior_days, resolved_epics}`: average days from creation
  to resolution for epics resolved in the window (`days`), the same for the
  prior window (`prior_days`, for a trend delta), and how many epics `days` is
  based on (`resolved_epics`). `days`/`prior_days` are `null` when no epic
  resolved in that window — treat as n/a. With few epics, one long/short epic
  swings it; read `resolved_epics` before trusting the number.
- `prior_period` holds delivery metrics (`backlog_delivered`,
  `throughput_per_week`) for the window *before* this one, so throughput renders
  with a trend delta instead of a context-free number. A big swing can be
  mix-shift (a few long-aged tickets closing) rather than a real process
  change — flag which.

## Active-epic detail (`recent_epics.linked`/`.other`)

Each epic carries, beyond `done`/`total`/`description`:

- `linked`/`other` are **sorted by Jira's native `Rank` field** (the real,
  team-set drag-and-drop backlog order) — the list order IS rank order. Epics
  carry no `priority` field at all: PART's `Priority` field sits at an unused
  default ("Lowest") on every epic and carries no signal, so ranking/ordering
  is derived from `Rank` (`customfield_10019` on this instance, a LexoRank
  string — sorted ascending) instead.
- `status` + `in_flight` (`true` when the epic is in Jira's "In Progress"
  status category — actively being worked, vs "New"/not-started or Done).
- `is_new` (`true` when the epic was created within the reporting window) and
  `is_done_recent` (`true` when the epic was resolved within the reporting
  window) — together these feed the slide's "Started epics | done (last Nd)"
  tile so both halves are genuinely time-boxed, not started-ever vs. done-ever.
- `rank_change` — the most recent Rank move within the window from changelog
  (`{when, direction}` with direction `raised`/`lowered`), or `null`. Jira only
  logs Rank changes as a bare direction ("Ranked higher"/"Ranked lower"), never
  an absolute from/to position, so there's no `from`/`to` value to carry (unlike
  the old priority-based version of this field).

## `pull_requests` (GitHub, optional)

Pulled from GitHub's Search API by `github_prs.py` when `GITHUB_TOKEN` and
`GITHUB_REPOS` are set in `.env` (`GITHUB_API_URL` defaults to
`https://api.github.com`; use `https://<host>/api/v3` for GitHub Enterprise /
internal "lava" hosts). When unset, `pull_requests` is
`{"configured": false, ...}` and an `auto_caveats` line says so — the Jira
report is unaffected. A GitHub error degrades to `configured: false` rather
than failing the run.

**Cross-link + sprint attribution.** Each PR carries `linked_issues` — Jira
keys parsed from the PR title/body (the team links the ticket in the PR
description). `by_sprint` groups PRs by the sprint of the ticket they link to,
and `current_sprint` is the active (or latest) sprint. Today PART has no sprint
field populated, so `current_sprint` is `null` and everything falls into a
`"(no sprint)"` bucket with `opened`/`merged` counts for the 30-day window
(a caveat flags this). The moment the sprint field is set, PRs group by sprint
automatically — no code change. The pptx tile shows "merged in
window | open now" (e.g. `10 | 3`) — deliberately *not* "opened", since
created-in-window collides with currently-open and misleads readers; `open_now`
reconciles with the repo's open-PR count. Sprint-scoped once a sprint exists.

## `sprint_goal` and `velocity_history`

Computed live in `jira_exec_summary.compute_sprint_stats` from the Sprint
(`customfield_10020`) and Story Points (`customfield_13078`) fields — not
hardcoded, so no code change is needed as the board moves through sprint
states:

- `sprint_goal` is non-null only while a sprint is **active**; it includes the
  sprint's `goal` text (often blank — PART hasn't been filling this in, flagged
  via `auto_caveats` when so) plus `committed_points`/`in_progress_points`/
  `completed_points` summed from Story Points on that sprint's issues.
  `committed_points` is the sprint total; `completed_points` is points on
  resolved issues; `in_progress_points` is points on unresolved issues in
  Jira's "In Progress" status category (the same `in_flight` convention
  `recent_epics` uses) — the remainder (`committed - completed - in_progress`)
  hasn't been started yet.
- `velocity_history` is non-null only once at least one sprint has **closed**
  (a real committed-vs-completed comparison needs a finished cycle) — a
  rolling window of the last 6 closed sprints.
- Both are **null, not zero or omitted**, whenever the underlying state isn't
  there yet (no sprint at all / sprint not started / no sprint closed) —
  report as "not tracked yet" (or skip the sub-question honestly), never
  estimate a number. `auto_caveats` always states which case applies.
- Report `throughput_per_week`/`epic_cycle_time` as the efficiency signal
  whenever these are null.

## Project-specific knowledge baked into the underlying engine

- Epics roll up to initiative `AA-431` ("Digital Health Partnerships – Phase 1
  Provider Focus") via `INITIATIVE_KEY`/`INITIATIVE_NAME` in
  `jira_exec_summary.py`. `recent_epics.other` are epics that exist in PART
  but don't roll up to that initiative (e.g. internal enablement) — keep
  these separate from initiative progress, don't blend them.
- `recent_epics.excluded` are epics filtered out as test/junk data (status
  "Discard" — currently just `PART-1`, "Sample Epic"). Don't resurrect these
  into a report. Status "Discard" also marks other one-off junk tickets
  project-wide (not just PART-1's children); these are excluded from
  `backlog_total`/`resolved_this_period`/`backlog_delivered`/
  `throughput_per_week` too, with an `auto_caveats` line naming which ones.
- `auto_caveats` already flags data-hygiene noise the underlying engine
  detects (e.g. a field stuck at 90%+ one value, or 50%+ unassigned) —
  check these before treating a raw number as a real finding.
- If you're pointing this at a different project, change `PROJECT` (and the
  window/trend constants) at the top of `fetch.py`, plus `INITIATIVE_KEY`,
  `INITIATIVE_NAME`, `EXCLUDED_STATUSES` in `jira_exec_summary.py` and
  `SPRINT_FIELD_ID`/`STORY_POINTS_FIELD_ID`/`RANK_FIELD_ID` in `jira_report.py`/
  `jira_exec_summary.py`. All three engine modules live in `scripts/`
  alongside `fetch.py`.

## Implementation

See `scripts/fetch.py`. It calls `jira_exec_summary.compute_stats` for most
categories and adds two direct queries that engine doesn't already produce:
`active_issues` (open, non-stale issues in the window) and `overdue` (past
due date, unresolved, not time-windowed).

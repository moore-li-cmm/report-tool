---
name: jira-data-fetch
description: >-
  Fetches structured Jira data for status reporting on the PART project —
  active issues, epic/initiative rollup, throughput, cycle time,
  flagged/stale/overdue issues. Wraps the project's existing Jira API layer
  (jira_report.py, jira_exec_summary.py) rather than reimplementing it. Use
  as the first step of building a status report, alongside data-format-report
  and the manager subagent.
---

# Jira data fetch

Produces one JSON file — the factual, ungrouped-by-narrative snapshot that
`data-format-report` and the `manager` subagent build on. No analysis or
prose happens here; this step is pure retrieval + aggregation.

Run from the repo root (where `.env` lives). Any interpreter with
`requirements.txt` installed works; use the venv's, since bare `python` isn't on
PATH here and a system `python3` may not have the deps:

```bash
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py
```

No arguments — the project (PART) and the 30-day reporting window are fixed
constants at the top of `fetch.py`, and it writes `data.json` to the repo root.

## Output contract

```json
{
  "project": "PART",
  "since_days": 30,
  "period_label": "Last 30 days",
  "generated_at": "...",
  "initiative_key": "AA-431",
  "initiative_name": "AA-431 — Digital Health Partnerships – Phase 1 Provider Focus",
  "initiative_description": "plain-text 'what/why' pulled from the initiative (AA-431) — no dedicated narrative field, available for the manager to reference in prose if useful",
  "initiative_status": "New",
  "sprint_goal": {"name": "PartInt Pilot 1", "goal": null, "start_date": "...", "end_date": "...", "committed_points": 26.0, "completed_points": 0, "in_progress_points": 8.0},
  "total_completed_points": 0,
  "backlog_total": 12,
  "backlog_delivered": 1,
  "epic_cycle_time": {"days": 14.0, "resolved_epics": 2},
  "throughput_per_week": 1.8,
  "recent_epics": {"linked": [{"key": "...", "summary": "...", "done": 0, "total": 0, "description": "epic goal text", "status": "Analyzing", "in_flight": true, "is_new": false, "is_done_recent": false, "rank_change": {"when": "2026-07-20", "direction": "raised"}}], "other": [...], "excluded": [...], "aged_out": [{"key": "...", "summary": "...", "resolved": "..."}]},
  "resolved_this_period": [{"key": "...", "summary": "...", "issuetype": "...", "description": "what actually shipped"}],
  "active_issues": [{"key": "...", "summary": "...", "status": "...", "issuetype": "...", "assignee": "..."}],
  "flagged": [{"issue": "...", "summary": "...", "status": "..."}],
  "stale": [{"key": "...", "summary": "...", "days_since_update": 21}],
  "overdue": [{"key": "...", "summary": "...", "status": "...", "duedate": "...", "days_overdue": 5}],
  "priority_breakdown": {"Lowest": 12},
  "assignee_breakdown": {"Unassigned": 4, "...": 8},
  "pull_requests": {"configured": true, "since": "2026-06-30", "repos": ["owner/repo"],
                    "opened_in_window": 6, "merged_in_window": 3, "open_now": 2,
                    "sprints": {}, "current_sprint": null,
                    "by_sprint": {"(no sprint)": {"opened": 6, "merged": 3, "prs": [...]}},
                    "prs": [{"number": 4, "merged": true, "linked_issues": ["PART-127"], "url": "...", "author": "..."}]},
  "auto_caveats": ["..."],
  "suggested_status": {"level": "good|warning|critical", "label": "..."}
}
```

**Epics age out of `recent_epics`.** An epic resolved *before* the reporting
window moves to `recent_epics.aged_out` instead of `linked`/`other`, so it gets
one last appearance — the window it completes in, where `is_done_recent` is
true — and then leaves the Active Epics panel rather than holding a rank slot
forever. Aged-out epics still scope the backlog and cycle-time numbers exactly
as before; only their panel/prose visibility changes. An epic whose status is
Done but which carries no `resolutiondate` can't be dated and stays visible.

## `pull_requests` (GitHub, optional)

Pulled by `github_prs.py` from the REST list-pulls endpoint
(`GET /repos/{owner}/{repo}/pulls?state=all`, newest-created first, capped at 5
pages of 100) when `GITHUB_TOKEN` and
`GITHUB_REPOS` are set in `.env` (`GITHUB_API_URL` defaults to
`https://api.github.com`; use `https://<host>/api/v3` for GitHub Enterprise /
internal "lava" hosts). When unset, `pull_requests` is
`{"configured": false, ...}` and an `auto_caveats` line says so — the Jira
report is unaffected. A GitHub error degrades to `configured: false` rather
than failing the run.

**Cross-link + sprint attribution.** Each PR carries `linked_issues` — Jira
keys parsed from the PR title/body (the team links the ticket in the PR
description). `by_sprint` groups PRs by the sprint of the ticket they link to;
`current_sprint` is the active (or latest) sprint. A PR whose linked ticket has
no sprint set falls into a `"(no sprint)"` bucket over the 30-day window (a
caveat flags this), and groups by sprint automatically once that ticket carries
one — no code change. The pptx tile shows "merged | open now" (e.g. `10 | 3`)
with the scope — the sprint name, or "last 30d" — as the tile's sub-line.
Deliberately *not* "opened", since created-in-window collides with
currently-open and misleads readers; `open_now` reconciles with the repo's
open-PR count.

For field semantics, scoping rules, and how to interpret these numbers when
writing a report, see CLAUDE.md's "Project-specific data quirks" section and
`manager.md`'s "Project-specific knowledge (PART)" section — this file covers
retrieval only.

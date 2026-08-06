#!/usr/bin/env python3
"""Fetch + compute structured Jira data for status reporting (jira-data-fetch skill).

Thin wrapper around the engine modules alongside it in this folder
(jira_exec_summary.compute_stats/search, jira_report.fetch_issues) — this script
does not re-implement Jira API calls, it adapts their output to the grouped-category
contract described in SKILL.md and adds the two queries that engine doesn't already
produce (a same-window "active issues" list and an explicit overdue list).

Fixed to the PART project over a 30-day window. Reads JIRA_BASE_URL / JIRA_EMAIL /
JIRA_API_TOKEN (and optional GITHUB_TOKEN / GITHUB_REPOS) from the repo-root .env
and writes data.json to the repo root.

Usage:
    ./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py
"""

from __future__ import annotations

import json
import os
from datetime import date

from dotenv import load_dotenv

from jira_exec_summary import compute_stats, search
from jira_report import SPRINT_FIELD_ID, JiraAuthError, latest_sprint, parse_jira_date, verify_auth
from github_prs import DEFAULT_API_URL, fetch_pr_stats

# Repo root is four levels up (scripts/ -> jira-data-fetch/ -> skills/ -> .claude/);
# used to locate .env and to write data.json there regardless of cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

# Fixed reporting scope: this tool reports the PART project over the last 30 days.
PROJECT = "PART"
SINCE_DAYS = 30
OUT_PATH = os.path.join(_REPO_ROOT, "data.json")


def _attach_sprint_attribution(pr: dict, base_url, email, token) -> None:
    """Group the fetched PRs by the sprint of the Jira ticket each one links to.

    PRs link their ticket in the PR body (parsed into `linked_issues` upstream).
    We look up those issues' Jira sprint field and bucket PRs by sprint name, so
    "PRs in sprint N" is answerable. If a linked ticket has no sprint set, it
    falls into a single "(no sprint)" bucket with a caveat instead.
    """
    keys = sorted({k for p in pr.get("prs", []) for k in p.get("linked_issues", [])})
    issue_sprint: dict[str, str] = {}
    sprints: dict[str, dict] = {}
    if keys:
        jql = "key in (%s)" % ",".join(keys)
        for iss in search(base_url, email, token, jql, [SPRINT_FIELD_ID]):
            chosen = latest_sprint(iss["fields"])
            if chosen:
                issue_sprint[iss["key"]] = chosen.get("name")
                sprints[chosen.get("name")] = {
                    "state": chosen.get("state"),
                    "start": chosen.get("startDate"),
                    "end": chosen.get("endDate"),
                }

    current = None
    active = [n for n, m in sprints.items() if (m.get("state") or "").lower() == "active"]
    if active:
        current = active[0]
    elif sprints:
        current = sorted(sprints, key=lambda n: sprints[n].get("end") or "")[-1]

    by_sprint: dict[str, dict] = {}
    for p in pr.get("prs", []):
        sname = next((issue_sprint[k] for k in p.get("linked_issues", []) if k in issue_sprint), None)
        bucket = by_sprint.setdefault(sname or "(no sprint)", {"opened": 0, "merged": 0, "prs": []})
        bucket["opened"] += 1
        if p.get("merged"):
            bucket["merged"] += 1
        bucket["prs"].append(p.get("number"))

    pr["sprints"] = sprints
    pr["current_sprint"] = current
    pr["by_sprint"] = by_sprint


def compute_active_issues(base_url: str, email: str, token: str, project: str, since_days: int) -> list[dict]:
    """Work that both STARTED and is still moving in the window: issues created in
    the last `since_days`, still unresolved, and touched in the last 14 days.

    Note the `created` clause — this is deliberately the newly-opened-and-active
    cohort, not every open ticket being worked. A ticket opened before the window
    and actively worked today is not here; it's in the (initiative-scoped) backlog
    and `stale` covers the neglected end. Unlike those, this list is
    project-wide, not initiative-scoped."""
    issues = search(
        base_url, email, token,
        f"project = {project} AND created >= -{since_days}d AND resolutiondate is EMPTY AND updated >= -14d",
        ["summary", "status", "issuetype", "assignee"],
    )
    return [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
            "issuetype": i["fields"]["issuetype"]["name"],
            "assignee": (i["fields"].get("assignee") or {}).get("displayName", "Unassigned"),
        }
        for i in issues
    ]


def compute_overdue(base_url: str, email: str, token: str, project: str) -> list[dict]:
    """Open issues past their due date, regardless of when created (not windowed)."""
    issues = search(
        base_url, email, token,
        f"project = {project} AND duedate is not EMPTY AND resolutiondate is EMPTY",
        ["summary", "status", "duedate"],
    )
    today = date.today()
    overdue = []
    for i in issues:
        due = parse_jira_date(i["fields"].get("duedate"))
        if due and due < today:
            overdue.append(
                {
                    "key": i["key"],
                    "summary": i["fields"]["summary"],
                    "status": i["fields"]["status"]["name"],
                    "duedate": due.isoformat(),
                    "days_overdue": (today - due).days,
                }
            )
    return overdue


def build_contract(base_url: str, email: str, token: str, project: str, since_days: int) -> dict:
    # compute_stats already returns: project, since_days, period_label, generated_at,
    # initiative_key, initiative_name, initiative_description, backlog_total,
    # backlog_delivered, epic_cycle_time, throughput_per_week,
    # epics, flagged, stale_tickets, resolved_this_period,
    # priority_breakdown, assignee_breakdown, auto_caveats, suggested_status.
    stats = compute_stats(base_url, email, token, project, since_days)

    stats["active_issues"] = compute_active_issues(base_url, email, token, project, since_days)
    stats["overdue"] = compute_overdue(base_url, email, token, project)

    # Aliases matching the six-category contract in SKILL.md, without dropping the
    # original keys — data-format-report's format_pptx.py reads a mix of both
    # (e.g. `flagged` original, `recent_epics` alias).
    stats["stale"] = stats["stale_tickets"]
    stats["recent_epics"] = stats["epics"]

    # sprint_goal / total_completed_points are computed live in compute_stats
    # (jira_exec_summary.compute_sprint_stats) from the Sprint/Story-Points
    # fields — null with a caveat when there's no active sprint / no sprint data
    # at all, populated automatically once one exists. Never fill either with a
    # guessed number.

    # GitHub PR activity (optional). Gated on config so a repo without GitHub set
    # up still gets a full Jira report. Windowed by the 30-day window; grouped
    # by sprint instead via `by_sprint`/`current_sprint` once a ticket has one.
    gh_token = os.environ.get("GITHUB_TOKEN")
    gh_repos = [r.strip() for r in os.environ.get("GITHUB_REPOS", "").split(",") if r.strip()]
    gh_api = os.environ.get("GITHUB_API_URL", DEFAULT_API_URL)
    if gh_token and gh_repos:
        pr = fetch_pr_stats(gh_api, gh_token, gh_repos, since_days, project)
        stats["pull_requests"] = pr
        if pr.get("configured"):
            _attach_sprint_attribution(pr, base_url, email, token)
            if not pr.get("sprints"):
                stats["auto_caveats"].append(
                    f"No Jira sprint is set on the PR-linked issues yet — PR counts are the "
                    f"last {since_days}d, not a sprint. They'll group by sprint automatically "
                    f"once the sprint field is populated."
                )
        else:
            stats["auto_caveats"].append(
                "GitHub PR fetch failed (check GITHUB_TOKEN/GITHUB_API_URL/repo access): "
                + pr.get("error", "unknown error")
            )
    else:
        stats["pull_requests"] = {"configured": False, "reason": "GITHUB_TOKEN and GITHUB_REPOS not set"}
        stats["auto_caveats"].append(
            "GitHub PR metrics not configured — set GITHUB_TOKEN and GITHUB_REPOS in .env "
            "to include PRs-per-window."
        )

    return stats


def main() -> None:
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    base_url = os.environ["JIRA_BASE_URL"]
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]

    # Fail loudly on bad credentials. Without this, Atlassian's anonymous
    # fallback (200 + empty issues) would write an all-zero data.json and the
    # report would falsely read "nothing delivered — at risk."
    try:
        verify_auth(base_url, email, token)
    except JiraAuthError as exc:
        raise SystemExit(f"ERROR: {exc}")

    data = build_contract(base_url, email, token, PROJECT, SINCE_DAYS)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

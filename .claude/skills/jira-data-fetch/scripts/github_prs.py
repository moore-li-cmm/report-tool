#!/usr/bin/env python3
"""Pull-request stats from GitHub for status reporting.

A peer of jira_report.py: a thin, dependency-light (requests only) data source.

Works against github.com *and* GitHub Enterprise (incl. internal "lava"-style
hosts) — set the API base URL accordingly:
    github.com          -> https://api.github.com   (default)
    GitHub Enterprise    -> https://<host>/api/v3

Config (env, read by fetch.py — this module just takes plain args):
    GITHUB_API_URL   optional; defaults to https://api.github.com
    GITHUB_TOKEN     a PAT with `repo` read scope
    GITHUB_REPOS     comma-separated "owner/repo" list, e.g. "covermymeds/dhc-pa-adapter"

Cross-linking: each returned PR carries `linked_issues` — the Jira keys parsed
out of its title + body (the team links the ticket in the PR **description**).
That's what lets fetch.py attribute a PR to the sprint of the ticket it
implemented. Always windowed by the reporting window in days; fetch.py then
groups those windowed PRs by the linked ticket's sprint, falling back to a
single "(no sprint)" bucket for tickets with no sprint set (see fetch.py).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_API_URL = "https://api.github.com"
_MAX_PAGES = 5  # bound the crawl so a large repo can't run away


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _list_pulls(api_url: str, token: str, repo: str, state: str) -> list[dict]:
    """PRs for a repo, newest-created first, capped at _MAX_PAGES * 100."""
    out: list[dict] = []
    page = 1
    while page <= _MAX_PAGES:
        resp = requests.get(
            f"{api_url.rstrip('/')}/repos/{repo}/pulls",
            params={"state": state, "sort": "created", "direction": "desc",
                    "per_page": 100, "page": page},
            headers=_headers(token),
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def fetch_pr_stats(api_url: str, token: str, repos: list[str], since_days: int,
                   project_key: str = "PART") -> dict:
    """Opened / merged / open PR counts over the last `since_days`, plus each
    windowed PR with the Jira keys it links to (for sprint attribution).

    Returns {"configured": False, ...} on any error rather than raising, so a
    GitHub outage or bad token can't sink an otherwise-good Jira report.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    key_re = re.compile(r"\b" + re.escape(project_key) + r"-\d+\b")
    prs: list[dict] = []
    open_now = 0
    try:
        for repo in repos:
            all_pulls = _list_pulls(api_url, token, repo, "all")
            open_now += sum(1 for p in all_pulls if p.get("state") == "open")
            for p in all_pulls:
                created, merged = _dt(p.get("created_at")), _dt(p.get("merged_at"))
                in_window = (created and created >= cutoff) or (merged and merged >= cutoff)
                if not in_window:
                    continue
                text = f"{p.get('title') or ''}\n{p.get('body') or ''}"
                prs.append({
                    "repo": repo,
                    "number": p.get("number"),
                    "title": p.get("title") or "",
                    "url": p.get("html_url", ""),
                    "author": (p.get("user") or {}).get("login", ""),
                    "state": p.get("state"),
                    "merged": bool(p.get("merged_at")),
                    "merged_at": p.get("merged_at"),
                    "created_at": p.get("created_at"),
                    "linked_issues": sorted(set(key_re.findall(text))),
                })
    except requests.RequestException as exc:
        return {"configured": False, "error": str(exc), "repos": repos}

    prs.sort(key=lambda p: p["number"] or 0, reverse=True)
    return {
        "configured": True,
        "since": cutoff.date().isoformat(),
        "repos": repos,
        "merged_in_window": sum(1 for p in prs if p["merged"] and _dt(p["merged_at"]) >= cutoff),
        "opened_in_window": sum(1 for p in prs if _dt(p["created_at"]) >= cutoff),
        "open_now": open_now,
        "prs": prs,
    }

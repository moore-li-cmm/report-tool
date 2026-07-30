#!/usr/bin/env python3
"""Jira Cloud data-access layer for the status-report pipeline.

A library module imported by jira_exec_summary.py and fetch.py — auth,
issue/changelog fetching, ADF-to-text flattening, and date parsing. Not a CLI.

Auth (Jira Cloud basic auth) is loaded from the repo-root .env by the caller:
    JIRA_BASE_URL   e.g. https://your-domain.atlassian.net
    JIRA_EMAIL      account email used to log in to Jira
    JIRA_API_TOKEN  API token for that account
"""

from __future__ import annotations

from datetime import date, datetime

import requests

SEARCH_URL_PATH = "/rest/api/3/search/jql"

# Confirmed via editmeta on real PART issues (Task/Epic/Story/Spike) — this
# instance has 1000+ custom fields and other projects may use a different ID.
SPRINT_FIELD_ID = "customfield_10020"


def parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def parse_jira_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def latest_sprint(fields: dict) -> dict | None:
    """Pick the sprint an issue currently belongs to from its Sprint field.

    The Sprint field (SPRINT_FIELD_ID) is a list — an issue accumulates every
    sprint it has passed through — so the last dict entry is its current sprint.
    Returns None when the field is empty or unset. Shared by fetch.py's PR-sprint
    attribution and jira_exec_summary's sprint/story-point stats so both bucket
    issues by sprint the same way."""
    raw = fields.get(SPRINT_FIELD_ID) or []
    raw = raw if isinstance(raw, list) else [raw]
    return next((s for s in reversed(raw) if isinstance(s, dict)), None)


def adf_to_text(node, _depth: int = 0) -> str:
    """Flatten an Atlassian Document Format (ADF) description to plain text.

    Jira Cloud stores rich-text fields (description, comments) as nested ADF
    JSON, not a string. We only need the human-readable text for an LLM to
    summarize, so this walks the tree collecting text/`text` nodes and inserts
    breaks between block-level nodes. Returns "" for an empty/missing field.
    """
    if not node:
        return ""
    if isinstance(node, str):
        return node
    parts: list[str] = []

    def walk(n) -> None:
        if isinstance(n, dict):
            ntype = n.get("type")
            if ntype == "text":
                parts.append(n.get("text", ""))
            elif ntype == "hardBreak":
                parts.append("\n")
            for child in n.get("content", []) or []:
                walk(child)
            # Block-level nodes get a trailing newline so paragraphs/list items
            # don't run together into one wall of text.
            if ntype in {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}:
                parts.append("\n")
        elif isinstance(n, list):
            for child in n:
                walk(child)

    walk(node)
    # Collapse runs of blank lines and trailing whitespace.
    text = "".join(parts)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def fetch_changelog(base_url: str, email: str, api_token: str, issue_key: str) -> list[dict]:
    """Return an issue's changelog `histories` (status transitions, etc.).

    The bulk `/search/jql` endpoint doesn't return changelog, so this is a
    per-issue GET with `expand=changelog`. Paginates the embedded changelog if
    the issue has more than one page of history. Returns [] on any HTTP error
    so a single bad issue can't sink a whole report run.
    """
    base = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}"
    try:
        response = requests.get(
            base,
            params={"expand": "changelog", "fields": "status"},
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        changelog = response.json().get("changelog", {})
    except requests.RequestException:
        return []

    histories = list(changelog.get("histories", []))
    total = changelog.get("total", len(histories))
    start = len(histories)
    while start < total:
        try:
            page = requests.get(
                f"{base}/changelog",
                params={"startAt": start, "maxResults": 100},
                auth=(email, api_token),
                headers={"Accept": "application/json"},
                timeout=30,
            )
            page.raise_for_status()
            values = page.json().get("values", [])
        except requests.RequestException:
            break
        if not values:
            break
        histories.extend(values)
        start += len(values)
    return histories


class JiraAuthError(RuntimeError):
    """Raised when Jira credentials are rejected. Exists because Atlassian's
    search/jql endpoint returns 200 {"issues": []} for a bad token (anonymous
    fallback) instead of erroring — so without an explicit auth probe, a dead
    token is indistinguishable from 'no work' and yields a false all-zero
    report. Callers should let this stop the run, never write a report."""


def verify_auth(base_url: str, email: str, api_token: str) -> dict:
    """Probe /myself so a rejected token fails loudly. Returns the account JSON
    on success; raises JiraAuthError otherwise. Call this BEFORE any search so
    the pipeline can't silently produce an all-zero 'at risk' report."""
    resp = requests.get(
        f"{base_url.rstrip('/')}/rest/api/3/myself",
        auth=(email, api_token),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    raise JiraAuthError(
        f"Jira auth failed ({resp.status_code}) for {email} at {base_url}. "
        "The API token is likely invalid or expired — generate a new one at "
        "https://id.atlassian.com/manage-profile/security/api-tokens and update "
        "JIRA_API_TOKEN in .env. (Not writing a report: a bad token would return "
        "empty results and render a false 'nothing delivered' slide.)"
    )


def fetch_issues(
    base_url: str, email: str, api_token: str, jql: str, fields: list[str]
) -> list[dict]:
    url = f"{base_url.rstrip('/')}{SEARCH_URL_PATH}"
    issues = []
    next_page_token = None

    while True:
        payload = {"jql": jql, "fields": fields, "maxResults": 100}
        if next_page_token:
            payload["nextPageToken"] = next_page_token

        response = requests.post(
            url,
            json=payload,
            auth=(email, api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        issues.extend(data.get("issues", []))
        next_page_token = data.get("nextPageToken")
        if not next_page_token or data.get("isLast"):
            break

    return issues

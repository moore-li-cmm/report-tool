#!/usr/bin/env python3
"""Compute PART exec-summary stats — the engine behind the report pipeline.

A library module wrapped by the jira-data-fetch and data-format-report skills
(see .claude/skills/) and orchestrated by the manager subagent
(.claude/agents/manager.md). `compute_stats` and `search` are the entry points
fetch.py imports. Pulls live Jira data and returns structured, factual stats —
no analysis and no rendering. Not a CLI.

Auth: same repo-root .env as jira_report.py.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from jira_report import SPRINT_FIELD_ID, adf_to_text, fetch_changelog, fetch_issues, parse_jira_datetime

# Confirmed via /rest/api/3/field on this instance: "Story Points" (distinct
# from the unused "Story point estimate", customfield_10016). Other projects
# may use a different ID.
STORY_POINTS_FIELD_ID = "customfield_13078"

# Confirmed via /rest/api/3/field on this instance: Jira Software's native
# "Rank" field (com.pyxis.greenhopper.jira:gh-lexo-rank) — the real, team-set
# backlog order (drag-and-drop), unlike Priority which sits at an unused
# default ("Lowest") for every PART epic and carries no signal. Epics are
# ordered by this field's LexoRank string, ascending. Other projects/instances
# may use a different ID.
RANK_FIELD_ID = "customfield_10019"

# How many closed sprints to show in the velocity history chart.
MAX_VELOCITY_SPRINTS = 6

# Confirmed empirically (see conversation): PART epics roll up to this
# cross-project Initiative. Other projects/instances will differ.
INITIATIVE_KEY = "AA-431"
INITIATIVE_NAME = "AA-431 — Digital Health Partnerships – Phase 1 Provider Focus"

# Confirmed empirically: "Discard" marks test/junk data on this project — both
# PART-1 "Sample Epic" (and its children PART-17/62/66/88) and standalone
# "delete me" stories under otherwise-real epics (e.g. PART-135/138/139/140
# under PART-128). Excluded wherever a terminal status could otherwise inflate
# a completion count (epic rollups, delivery/throughput, epic cycle time).
EXCLUDED_STATUSES = {"discard"}
DONE_STATUS_NAMES = {"done", "closed", "resolved", "discard", "cancelled", "canceled"}

def search(base_url, email, token, jql, fields):
    return fetch_issues(base_url, email, token, jql, fields=fields)


# ---------------------------------------------------------------------------
# Phase 1 — fetch + compute (factual, no analysis)
# ---------------------------------------------------------------------------


def _recent_rank_change(base_url, email, token, key, cutoff) -> dict | None:
    """Most recent backlog-Rank move on an epic within the window, from changelog.

    Jira's Rank field (LexoRank) only ever logs a directional toString —
    "Ranked higher"/"Ranked lower" — never the actual from/to position, so
    unlike a priority change there's no from/to label to carry, just when and
    which way. Needs a per-issue changelog GET (bulk search omits history) —
    cheap here since it's only run over epics (~10), not the whole backlog."""
    latest = None
    for h in fetch_changelog(base_url, email, token, key):
        when = parse_jira_datetime(h.get("created"))
        if not when or when < cutoff:
            continue
        for item in h.get("items", []):
            if item.get("field") == "Rank" and (latest is None or when > latest[0]):
                latest = (when, item.get("toString"))
    if not latest:
        return None
    when, to_string = latest
    direction = "raised" if "higher" in (to_string or "").lower() else "lowered"
    return {"when": when.date().isoformat(), "direction": direction}


def compute_epics(base_url, email, token, project, since_days) -> dict:
    epics = search(
        base_url, email, token,
        f"project = {project} AND issuetype = Epic",
        ["summary", "status", "parent", "description", RANK_FIELD_ID, "created", "resolutiondate"],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    linked, other, excluded = [], [], []
    # Epics that roll up to a real Initiative — used to scope the backlog to
    # initiative-connected work (loose/orphan tickets are dropped from backlog).
    initiative_epic_keys: set[str] = set()
    for e in epics:
        f = e["fields"]
        status = f["status"]["name"]
        if status.lower() in EXCLUDED_STATUSES:
            excluded.append({"key": e["key"], "summary": f["summary"], "status": status})
            continue
        parent = f.get("parent")
        parent_type = ((parent or {}).get("fields", {}).get("issuetype") or {}).get("name")
        if parent and parent_type == "Initiative":
            initiative_epic_keys.add(e["key"])
        target = linked if parent and parent.get("key") == INITIATIVE_KEY else other
        children = search(base_url, email, token, f"parent = {e['key']}", ["resolutiondate"])
        done = sum(1 for c in children if c["fields"].get("resolutiondate"))
        created = parse_jira_datetime(f.get("created"))
        resolved = parse_jira_datetime(f.get("resolutiondate"))
        target.append(
            {
                "key": e["key"],
                "summary": f["summary"],
                "done": done,
                "total": len(children),
                # Plain-text goal/scope so the LLM can describe the work and its
                # value from real source text, not just the one-line title.
                "description": adf_to_text(f.get("description")),
                # Internal-only LexoRank sort key (popped below) — real,
                # team-set backlog order, not the unused Priority field.
                "_rank": f.get(RANK_FIELD_ID) or "",
                "status": status,
                # In flight = actively being worked (Jira "In Progress" category),
                # vs "New"/backlog (not started) or Done.
                "in_flight": ((f["status"].get("statusCategory") or {}).get("key") == "indeterminate"),
                "is_new": bool(created and created >= cutoff),
                # Epic-level "done in this window" — pairs with is_new so the
                # slide's "started | done" tile is genuinely last-N-days on both
                # halves, not started-ever vs. started-ever.
                "is_done_recent": bool(resolved and resolved >= cutoff),
                "rank_change": _recent_rank_change(base_url, email, token, e["key"], cutoff),
            }
        )
    # Real backlog order: LexoRank strings sort correctly as plain strings.
    linked.sort(key=lambda x: x["_rank"])
    other.sort(key=lambda x: x["_rank"])
    for e in linked + other:
        del e["_rank"]
    return {
        "linked": linked,
        "other": other,
        "excluded": excluded,
        "initiative_epic_keys": sorted(initiative_epic_keys),
    }


def compute_blockers(base_url, email, token, project) -> list[dict]:
    issues = search(base_url, email, token, f"project = {project}", ["issuelinks"])
    blockers = []
    for issue in issues:
        for link in issue["fields"].get("issuelinks") or []:
            other = link.get("outwardIssue") or link.get("inwardIssue")
            if not other:
                continue
            direction = "outward" if "outwardIssue" in link else "inward"
            phrase = link["type"].get(direction) or ""
            other_status = other["fields"]["status"]["name"]
            if phrase.lower() == "is blocked by" and other_status.lower() not in DONE_STATUS_NAMES:
                blockers.append(
                    {"issue": issue["key"], "blocked_by": other["key"], "blocked_by_status": other_status}
                )
    return blockers


def compute_trend(base_url, email, token, project, weeks: int) -> dict:
    issues = search(
        base_url, email, token,
        f"project = {project} AND resolutiondate is not EMPTY",
        ["resolutiondate"],
    )
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    buckets: dict[date, int] = defaultdict(int)
    for issue in issues:
        resolved = parse_jira_datetime(issue["fields"]["resolutiondate"])
        if resolved and resolved >= cutoff:
            week_start = resolved.date() - timedelta(days=resolved.date().weekday())
            buckets[week_start] += 1

    today = datetime.now(timezone.utc).date()
    this_week_start = today - timedelta(days=today.weekday())
    ordered_weeks = [this_week_start - timedelta(weeks=w) for w in range(weeks - 1, -1, -1)]
    counts = [buckets.get(w, 0) for w in ordered_weeks]
    return {"week_labels": [w.isoformat() for w in ordered_weeks], "counts": counts}


# Work items counted as "backlog" and "delivered": epics excluded (boards list
# them separately — this is what makes backlog match the board count),
# sub-tasks excluded (they double-count their parent's work), and EXCLUDED_STATUSES
# (test/junk "Discard" tickets — not all under the excluded Sample Epic; some
# are individually discarded stories under real epics) excluded so they don't
# inflate delivery/throughput.
_WORKITEM_FILTER = "issuetype not in (Epic, Sub-task) AND status not in (%s)" % ", ".join(
    f'"{s}"' for s in EXCLUDED_STATUSES
)


def _delivery_metrics(resolved_issues: list[dict], since_days: int) -> dict:
    """Delivery numbers over the set of issues RESOLVED in a window — regardless
    of when they were created. That's true throughput; the old 'created AND
    resolved in the same window' cohort silently undercounted work started
    earlier and finished this period. (Cycle time is reported at the epic level
    now — see compute_epic_cycle_time — not per work item.)"""
    delivered = len(resolved_issues)
    return {
        "backlog_delivered": delivered,
        "throughput_per_week": round(delivered / (since_days / 7), 1),
    }


def compute_prior_period(base_url, email, token, project, since_days, initiative_epic_keys) -> dict:
    """Delivery metrics for the window *before* this one (issues resolved in that
    window), so KPI tiles can show a trend delta against a real baseline. Scoped
    to initiative-epic children the same way the current window is, so the
    delta compares like with like."""
    jql = (
        f"project = {project} AND {_WORKITEM_FILTER} "
        f"AND resolutiondate >= -{2 * since_days}d AND resolutiondate < -{since_days}d"
    )
    issues = search(base_url, email, token, jql, ["resolutiondate", "parent"])
    issues = [i for i in issues if (i["fields"].get("parent") or {}).get("key") in initiative_epic_keys]
    return _delivery_metrics(issues, since_days)


def compute_epic_cycle_time(base_url, email, token, project, since_days, initiative_epic_keys) -> dict:
    """Average days from creation to resolution for EPICS resolved in the current
    window, plus the prior window for a trend delta. This is the headline
    "how long an epic takes end-to-end" number the slide reports as cycle time —
    a longer-horizon signal than per-ticket cycle time. Test/discarded epics are
    excluded (they'd be a meaningless zero-effort resolution), as are epics that
    don't roll up to a real Initiative — same scoping as backlog/delivered, so
    an epic outside the tracked initiatives can't drive this number."""

    def _avg(start_days: int, end_days: int) -> tuple[float | None, int]:
        jql = f"project = {project} AND issuetype = Epic AND resolutiondate >= -{start_days}d"
        if end_days:
            jql += f" AND resolutiondate < -{end_days}d"
        epics = search(base_url, email, token, jql, ["created", "resolutiondate", "status"])
        days = [
            (
                parse_jira_datetime(e["fields"]["resolutiondate"])
                - parse_jira_datetime(e["fields"]["created"])
            ).days
            for e in epics
            if e["fields"]["status"]["name"].lower() not in EXCLUDED_STATUSES
            and e["key"] in initiative_epic_keys
        ]
        return (round(sum(days) / len(days), 1) if days else None, len(days))

    days, resolved = _avg(since_days, 0)
    prior_days, _ = _avg(2 * since_days, since_days)
    return {"days": days, "prior_days": prior_days, "resolved_epics": resolved}


def compute_sprint_stats(base_url, email, token, project) -> dict:
    """Live sprint_goal + velocity_history, derived from the Sprint/Story-Points
    fields rather than hardcoded. Whatever state those fields are actually in —
    no sprint yet, a sprint that hasn't started, an active sprint, or several
    closed ones — falls out of this query automatically. No code change is
    needed as PART's board moves from "no sprints" through "active sprint" to
    "several closed sprints"; only the caveat text differs.
    """
    issues = search(
        base_url, email, token,
        f'project = {project} AND "Sprint" is not EMPTY',
        [SPRINT_FIELD_ID, STORY_POINTS_FIELD_ID, "resolutiondate", "status"],
    )

    # A sprint field is a list (an issue can pass through multiple sprints as it
    # moves) — attribute each issue to the LAST sprint it was in, same
    # convention fetch.py's PR-sprint attribution uses.
    sprints: dict[int, dict] = {}
    committed: dict[int, float] = defaultdict(float)
    completed: dict[int, float] = defaultdict(float)
    in_progress: dict[int, float] = defaultdict(float)
    for i in issues:
        raw = i["fields"].get(SPRINT_FIELD_ID) or []
        raw = raw if isinstance(raw, list) else [raw]
        chosen = next((s for s in reversed(raw) if isinstance(s, dict)), None)
        if not chosen:
            continue
        sid = chosen.get("id")
        sprints[sid] = chosen
        points = i["fields"].get(STORY_POINTS_FIELD_ID) or 0
        committed[sid] += points
        if i["fields"].get("resolutiondate"):
            completed[sid] += points
        elif ((i["fields"].get("status") or {}).get("statusCategory") or {}).get("key") == "indeterminate":
            # Jira "In Progress" category, not yet resolved — the same in_flight
            # convention compute_epics uses for epics.
            in_progress[sid] += points

    caveats: list[str] = []

    # sprint_goal is only meaningful once a sprint is actually running.
    active = [s for s in sprints.values() if (s.get("state") or "").lower() == "active"]
    sprint_goal = None
    if active:
        s = active[0]
        sprint_goal = {
            "name": s.get("name"),
            "goal": s.get("goal") or None,
            "start_date": s.get("startDate"),
            "end_date": s.get("endDate"),
            "committed_points": committed.get(s.get("id"), 0),
            "completed_points": completed.get(s.get("id"), 0),
            "in_progress_points": in_progress.get(s.get("id"), 0),
        }
        if not s.get("goal"):
            caveats.append(f'Sprint "{s.get("name")}" is active but has no goal set in Jira.')
    elif sprints:
        upcoming = sorted(sprints.values(), key=lambda s: s.get("startDate") or "")[0]
        caveats.append(
            f'Sprint "{upcoming.get("name")}" exists (state: {upcoming.get("state")}) but hasn\'t '
            "started — sprint_goal stays null until it's active."
        )
    else:
        caveats.append("No Sprint field populated on this project yet — sprint_goal stays null.")

    # velocity_history only counts CLOSED sprints — a completed cycle is the
    # whole point of a velocity chart, an in-progress one isn't comparable yet.
    closed = sorted(
        (s for s in sprints.values() if (s.get("state") or "").lower() == "closed"),
        key=lambda s: s.get("endDate") or "",
    )[-MAX_VELOCITY_SPRINTS:]
    if closed:
        velocity_history = {
            "sprint_labels": [s.get("name") for s in closed],
            "committed": [committed.get(s.get("id"), 0) for s in closed],
            "completed": [completed.get(s.get("id"), 0) for s in closed],
        }
    else:
        velocity_history = None
        caveats.append("No completed sprint yet — velocity_history stays null until one closes.")

    if sprints and not any(committed.values()):
        caveats.append("Sprint field is populated but Story Points aren't estimated on any ticket yet.")

    # Total completed points across every sprint the project has ever run
    # (active, closed, or future) — not scoped to whichever sprint is
    # currently active, so it doesn't reset/change meaning as sprints roll over.
    # None (not 0) when the Sprint field has never been populated at all, so
    # "no sprint data yet" isn't confused with "zero points completed".
    total_completed_points = sum(completed.values()) if sprints else None

    return {
        "sprint_goal": sprint_goal,
        "velocity_history": velocity_history,
        "total_completed_points": total_completed_points,
        "caveats": caveats,
    }


def compute_stats(base_url, email, token, project, since_days, trend_weeks) -> dict:
    # Epics first — we need the set of epics that roll up to a real Initiative in
    # order to scope the backlog to initiative-connected work below.
    epics = compute_epics(base_url, email, token, project, since_days)
    initiative_epic_keys = set(epics.pop("initiative_epic_keys"))

    # --- Current open backlog (a snapshot of state, NOT windowed): open work
    # items whose parent epic rolls up to a real Initiative. Loose/orphan tickets
    # (no epic, or an epic outside any initiative) are excluded on purpose. ---
    open_all = search(
        base_url, email, token,
        f"project = {project} AND statusCategory != Done AND {_WORKITEM_FILTER}",
        ["issuetype", "assignee", "priority", "created", "updated", "status", "summary", "parent"],
    )
    open_issues = [
        i for i in open_all
        if (i["fields"].get("parent") or {}).get("key") in initiative_epic_keys
    ]
    backlog_total = len(open_issues)
    backlog_excluded = len(open_all) - backlog_total

    # --- Delivery IN the window: issues resolved in the last since_days, whenever
    # they were created — real throughput, not a created-and-closed cohort.
    # Scoped to initiative-epic children, same as the open backlog above, so
    # "delivered"/"throughput" reconcile with "backlog" instead of counting a
    # wider (whole-project) set than the backlog they're paired with on the slide. ---
    resolved_in_period_all = search(
        base_url, email, token,
        f"project = {project} AND {_WORKITEM_FILTER} AND resolutiondate >= -{since_days}d",
        ["issuetype", "resolutiondate", "summary", "description", "parent"],
    )
    resolved_in_period = [
        i for i in resolved_in_period_all
        if (i["fields"].get("parent") or {}).get("key") in initiative_epic_keys
    ]
    resolved_excluded = len(resolved_in_period_all) - len(resolved_in_period)
    delivery = _delivery_metrics(resolved_in_period, since_days)
    backlog_delivered = delivery["backlog_delivered"]
    throughput_per_week = delivery["throughput_per_week"]

    # How many EXCLUDED_STATUSES (test/junk) items resolved in-window were left
    # out of the count above — surfaced as a caveat so the exclusion isn't silent.
    excluded_status_list = ", ".join(f'"{s}"' for s in EXCLUDED_STATUSES)
    discarded_in_period = search(
        base_url, email, token,
        f"project = {project} AND issuetype not in (Epic, Sub-task) "
        f"AND status in ({excluded_status_list}) AND resolutiondate >= -{since_days}d",
        ["summary"],
    )

    # --- Stale: OPEN items untouched >= 14 days, over the real backlog (old
    # forgotten tickets are the whole point — a created-in-window cohort by
    # definition can't contain anything 14+ days stale). ---
    now = datetime.now(timezone.utc)
    stale = []
    for i in open_issues:
        updated = parse_jira_datetime(i["fields"].get("updated"))
        if updated and (now - updated).days >= 14:
            stale.append(
                {
                    "key": i["key"],
                    "summary": i["fields"]["summary"],
                    "days_since_update": (now - updated).days,
                }
            )

    resolved_summaries = [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "issuetype": i["fields"]["issuetype"]["name"],
            # What actually shipped — impact bullets must be grounded in these,
            # NOT in aspirational epic goals. Often empty on small/plumbing work.
            "description": adf_to_text(i["fields"].get("description")),
        }
        for i in resolved_in_period
    ]

    # Data-hygiene breakdowns over the current open backlog.
    priority_counts = Counter(
        (i["fields"].get("priority") or {}).get("name", "None") for i in open_issues
    )
    assignee_counts = Counter(
        i["fields"]["assignee"]["displayName"] if i["fields"].get("assignee") else "Unassigned"
        for i in open_issues
    )

    auto_caveats = []
    if priority_counts and backlog_total and priority_counts.most_common(1)[0][1] / backlog_total > 0.9:
        name, n = priority_counts.most_common(1)[0]
        auto_caveats.append(
            f'Priority is {n}/{backlog_total} "{name}" — likely an unused default, not real signal.'
        )
    unassigned = assignee_counts.get("Unassigned", 0)
    if backlog_total and unassigned / backlog_total > 0.5:
        auto_caveats.append(
            f"{unassigned}/{backlog_total} tickets are unassigned — likely triage/data-hygiene, "
            "not literal unassigned workload."
        )
    sprint_stats = compute_sprint_stats(base_url, email, token, project)
    auto_caveats.extend(sprint_stats["caveats"])

    if backlog_excluded:
        auto_caveats.append(
            f"Backlog counts only initiative-connected tickets: {backlog_excluded} open ticket(s) "
            "with no parent epic under a real initiative are excluded from backlog_total."
        )
    if resolved_excluded:
        auto_caveats.append(
            f"{resolved_excluded} resolved ticket(s) in the window have no parent epic under a real "
            "initiative — excluded from backlog_delivered/throughput_per_week/resolved_this_period, "
            "same scoping as backlog_total."
        )

    # Epics were computed at the top of this function (needed for backlog scoping).
    if epics["excluded"]:
        keys = ", ".join(f'"{e["key"]}"' for e in epics["excluded"])
        auto_caveats.append(f"{keys} excluded from epic rollups (status indicates test/discarded data).")

    non_initiative_done = [e for e in epics["other"] if e.get("is_done_recent")]
    if non_initiative_done:
        keys = ", ".join(f'"{e["key"]}"' for e in non_initiative_done)
        auto_caveats.append(
            f"Epic(s) {keys} resolved in-window but don't roll up to a real initiative — excluded "
            "from epic_cycle_time, same scoping as backlog_delivered."
        )

    if discarded_in_period:
        keys = ", ".join(f'"{i["key"]}"' for i in discarded_in_period)
        auto_caveats.append(
            f"{len(discarded_in_period)} ticket(s) resolved in-window with status Discard ({keys}) are "
            "test/junk data (not all under the excluded Sample Epic — some are individually discarded "
            "stories under real epics) and are excluded from resolved_this_period/backlog_delivered/"
            "throughput_per_week — not counted as real delivery."
        )

    blockers = compute_blockers(base_url, email, token, project)
    trend = compute_trend(base_url, email, token, project, trend_weeks)

    # Business-value source text lives on the initiative, not in any PART issue.
    # Pull it so the LLM can ground the mission_line in real text.
    init_issues = search(base_url, email, token, f"key = {INITIATIVE_KEY}", ["description", "status"])
    initiative_description = (
        adf_to_text(init_issues[0]["fields"].get("description")) if init_issues else ""
    )
    # The initiative's own Jira status = the project-level status — distinct
    # from any sprint status (which lives in sprint_goal, see compute_sprint_stats).
    initiative_status = (
        init_issues[0]["fields"]["status"]["name"] if init_issues else None
    )

    prior_period = compute_prior_period(base_url, email, token, project, since_days, initiative_epic_keys)
    epic_cycle_time = compute_epic_cycle_time(base_url, email, token, project, since_days, initiative_epic_keys)

    # Overall DELIVERY health of the team against initiative AA-431 (a red/amber/
    # green verdict) — NOT sprint or goal status (sprint_goal is a separate field).
    # The label states the reason so red/amber is never unexplained. Naive default;
    # the manager can override class+label from the fuller picture.
    if blockers:
        suggested_status = {"class": "dot--critical", "label": "At risk: active blocker"}
    elif backlog_delivered == 0:
        suggested_status = {"class": "dot--warning", "label": "Watch: nothing delivered this period"}
    else:
        suggested_status = {"class": "dot--good", "label": "On track"}

    return {
        "project": project,
        "since_days": since_days,
        "period_label": f"Last {since_days} days",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "initiative_key": INITIATIVE_KEY,
        "initiative_name": INITIATIVE_NAME,
        "initiative_description": initiative_description,
        "initiative_status": initiative_status,
        "sprint_goal": sprint_stats["sprint_goal"],
        "velocity_history": sprint_stats["velocity_history"],
        "total_completed_points": sprint_stats["total_completed_points"],
        "backlog_total": backlog_total,
        "backlog_delivered": backlog_delivered,
        "epic_cycle_time": epic_cycle_time,
        "throughput_per_week": throughput_per_week,
        "prior_period": prior_period,
        "epics": epics,
        "blockers": blockers,
        "trend": trend,
        "stale_tickets": stale,
        "resolved_this_period": resolved_summaries,
        "priority_breakdown": dict(priority_counts),
        "assignee_breakdown": dict(assignee_counts),
        "auto_caveats": auto_caveats,
        "suggested_status": suggested_status,
    }

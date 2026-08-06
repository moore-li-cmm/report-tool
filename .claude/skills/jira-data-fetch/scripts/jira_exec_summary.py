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
from datetime import datetime, timedelta, timezone

from jira_report import (
    SPRINT_FIELD_ID,
    adf_to_text,
    fetch_changelog,
    fetch_issues,
    latest_sprint,
    parse_jira_datetime,
)

# Confirmed via /rest/api/3/field on this instance: "Story Points"  
# Other projects may use a different ID.
STORY_POINTS_FIELD_ID = "customfield_13078"

# "Rank" field (com.pyxis.greenhopper.jira:gh-lexo-rank). Epics are
# ordered by this field's LexoRank string, ascending. 
# Other projects/instances may use a different ID.
RANK_FIELD_ID = "customfield_10019"

# Confirmed empirically (see conversation): PART epics roll up to this
# cross-project Initiative. Other projects/instances will differ.
INITIATIVE_KEY = "AA-431"
INITIATIVE_NAME = "AA-431 — Digital Health Partnerships - Phase 1 Provider Focus"

# Excluded wherever a terminal status could otherwise inflate
# a completion count (epic rollups, delivery/throughput, epic cycle time).
EXCLUDED_STATUSES = {"discard"}
DONE_STATUS_NAMES = {"done", "discard", "cancelled", "holding tank"}

def search(base_url, email, token, jql, fields):
    return fetch_issues(base_url, email, token, jql, fields=fields)


def _under_initiative(issue: dict, epic_keys: set[str]) -> bool:
    """True if the issue's parent epic rolls up to a tracked initiative — the
    scoping rule shared by backlog_total and backlog_delivered, so both
    reconcile against the same set of work."""
    return (issue["fields"].get("parent") or {}).get("key") in epic_keys


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


def _child_counts(base_url, email, token, epic_keys) -> dict[str, tuple[int, int]]:
    """{epic key: (done, total)} for every epic, in ONE query rather than one per
    epic. `parent in (...)` returns all children at once and each child names its
    own parent, so they group locally — a per-epic search here was the pipeline's
    biggest source of round trips."""
    if not epic_keys:
        return {}
    counts: dict[str, list[int]] = {k: [0, 0] for k in epic_keys}
    jql = "parent in (%s)" % ", ".join(sorted(epic_keys))
    for c in search(base_url, email, token, jql, ["parent", "resolutiondate"]):
        parent_key = (c["fields"].get("parent") or {}).get("key")
        if parent_key not in counts:
            continue
        counts[parent_key][1] += 1
        if c["fields"].get("resolutiondate"):
            counts[parent_key][0] += 1
    return {k: (done, total) for k, (done, total) in counts.items()}


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
    child_counts = _child_counts(
        base_url, email, token,
        [e["key"] for e in epics if e["fields"]["status"]["name"].lower() not in EXCLUDED_STATUSES],
    )
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
        done, total = child_counts.get(e["key"], (0, 0))
        created = parse_jira_datetime(f.get("created"))
        resolved = parse_jira_datetime(f.get("resolutiondate"))
        target.append(
            {
                "key": e["key"],
                "summary": f["summary"],
                "done": done,
                "total": total,
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
                # slide's "created | completed" tile is genuinely last-N-days on
                # both halves, not started-ever vs. started-ever.
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


def compute_flagged(base_url, email, token, project) -> list[dict]:
    """Issues carrying Jira's native Flagged/Impediment marker — the same signal
    the flag icon on the board/backlog shows. Excludes issues whose own status
    is already Done/Discarded (a stale flag left on closed work isn't a live risk)."""
    issues = search(
        base_url, email, token,
        f"project = {project} AND flagged is not EMPTY",
        ["summary", "status"],
    )
    return [
        {"issue": i["key"], "summary": i["fields"].get("summary", ""), "status": i["fields"]["status"]["name"]}
        for i in issues
        if i["fields"]["status"]["name"].lower() not in DONE_STATUS_NAMES
    ]


# Work items counted as "backlog" and "delivered": epics excluded (boards list
# them separately — this is what makes backlog match the board count),
# sub-tasks excluded (they double-count their parent's work), and EXCLUDED_STATUSES
# (test/junk "Discard" tickets — not all under the excluded Sample Epic; some
# are individually discarded stories under real epics) excluded so they don't
# inflate delivery/throughput.
_WORKITEM_FILTER = "issuetype not in (Epic, Sub-task) AND status not in (%s)" % ", ".join(
    f'"{s}"' for s in EXCLUDED_STATUSES
)


def compute_epic_cycle_time(base_url, email, token, project, since_days, initiative_epic_keys) -> dict:
    """Average days from creation to resolution for EPICS resolved in the
    reporting window. This is the headline "how long an epic takes end-to-end"
    number the slide reports as cycle time — a longer-horizon signal than
    per-ticket cycle time. Test/discarded epics are excluded (they'd be a
    meaningless zero-effort resolution), as are epics that don't roll up to a
    real Initiative — same scoping as backlog/delivered, so an epic outside the
    tracked initiatives can't drive this number."""
    epics = search(
        base_url, email, token,
        f"project = {project} AND issuetype = Epic AND resolutiondate >= -{since_days}d",
        ["created", "resolutiondate", "status"],
    )
    days = [
        (
            parse_jira_datetime(e["fields"]["resolutiondate"])
            - parse_jira_datetime(e["fields"]["created"])
        ).days
        for e in epics
        if e["fields"]["status"]["name"].lower() not in EXCLUDED_STATUSES
        and e["key"] in initiative_epic_keys
    ]
    return {
        "days": round(sum(days) / len(days), 1) if days else None,
        "resolved_epics": len(days),
    }


def compute_sprint_stats(base_url, email, token, project) -> dict:
    """Live sprint_goal + total_completed_points, derived from the Sprint/
    Story-Points fields rather than hardcoded. Whatever state those fields are
    actually in — no sprint yet, a sprint that hasn't started, an active sprint,
    or several closed ones — falls out of this query automatically. No code
    change is needed as PART's board moves from "no sprints" through "active
    sprint" to "several closed sprints"; only the caveat text differs.
    """
    issues = search(
        base_url, email, token,
        f'project = {project} AND "Sprint" is not EMPTY',
        [SPRINT_FIELD_ID, STORY_POINTS_FIELD_ID, "resolutiondate", "status"],
    )

    # latest_sprint() attributes each issue to the last sprint it was in — the
    # same rule fetch.py's PR-sprint attribution uses.
    sprints: dict[int, dict] = {}
    committed: dict[int, float] = defaultdict(float)
    completed: dict[int, float] = defaultdict(float)
    in_progress: dict[int, float] = defaultdict(float)
    for i in issues:
        chosen = latest_sprint(i["fields"])
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
        "total_completed_points": total_completed_points,
        "caveats": caveats,
    }


def compute_stats(base_url, email, token, project, since_days) -> dict:
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
    open_issues = [i for i in open_all if _under_initiative(i, initiative_epic_keys)]
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
    resolved_in_period = [i for i in resolved_in_period_all if _under_initiative(i, initiative_epic_keys)]
    resolved_excluded = len(resolved_in_period_all) - len(resolved_in_period)
    # Delivery counts issues RESOLVED in the window regardless of when they were
    # created. That's true throughput; a 'created AND resolved in the same
    # window' cohort would silently undercount work started earlier and finished
    # this period. (Cycle time is reported at the epic level — see
    # compute_epic_cycle_time — not per work item.)
    backlog_delivered = len(resolved_in_period)
    throughput_per_week = round(backlog_delivered / (since_days / 7), 1)

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

    flagged = compute_flagged(base_url, email, token, project)

    # Business-value source text lives on the initiative, not in any PART issue.
    # No dedicated narrative field consumes this — available for the manager
    # to reference in prose if useful.
    init_issues = search(base_url, email, token, f"key = {INITIATIVE_KEY}", ["description", "status"])
    initiative_description = (
        adf_to_text(init_issues[0]["fields"].get("description")) if init_issues else ""
    )
    # The initiative's own Jira status = the project-level status — distinct
    # from any sprint status (which lives in sprint_goal, see compute_sprint_stats).
    initiative_status = (
        init_issues[0]["fields"]["status"]["name"] if init_issues else None
    )

    epic_cycle_time = compute_epic_cycle_time(base_url, email, token, project, since_days, initiative_epic_keys)

    # Overall DELIVERY health of the team against initiative AA-431 (a red/amber/
    # green verdict) — NOT sprint or goal status (sprint_goal is a separate field).
    # The label states the reason so red/amber is never unexplained. Naive default,
    # and reference-only: the slide's Project-health tile is a manual fill-in, so
    # nothing renders this. The manager may cite or override it in prose.
    if flagged:
        suggested_status = {"level": "critical", "label": "At risk: ticket flagged in Jira"}
    elif backlog_delivered == 0:
        suggested_status = {"level": "warning", "label": "Watch: nothing delivered this period"}
    else:
        suggested_status = {"level": "good", "label": "On track"}

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
        "total_completed_points": sprint_stats["total_completed_points"],
        "backlog_total": backlog_total,
        "backlog_delivered": backlog_delivered,
        "epic_cycle_time": epic_cycle_time,
        "throughput_per_week": throughput_per_week,
        "epics": epics,
        "flagged": flagged,
        "stale_tickets": stale,
        "resolved_this_period": resolved_summaries,
        "priority_breakdown": dict(priority_counts),
        "assignee_breakdown": dict(assignee_counts),
        "auto_caveats": auto_caveats,
        "suggested_status": suggested_status,
    }

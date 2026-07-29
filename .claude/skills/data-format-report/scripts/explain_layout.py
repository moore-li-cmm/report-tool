#!/usr/bin/env python3
"""Render a reference slide showing HOW each field on exec_summary.pptx is
derived — same layout/geometry as format_pptx.py, but every tile/panel holds
an explanation (source query, function, file) instead of real data.

Not part of the reporting pipeline: nothing reads data.json/narrative.json
here, and no other script depends on this one's output. Exists purely as a
documentation artifact — a live audit of where each number on the real slide
comes from, in the same spatial layout so it's easy to compare side by side.

Usage:
    ./.venv/bin/python .claude/skills/data-format-report/scripts/explain_layout.py
"""

from __future__ import annotations

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Emu, Inches, Pt

from format_pptx import (
    HEADER_GREEN,
    MARGIN,
    SLIDE_H,
    SLIDE_W,
    TEXT_DARK,
    TEXT_GRAY,
    TILE_BORDER,
    WHITE,
    _para,
    _tf,
    bullets,
    section_header,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
OUT_PATH = os.path.join(_REPO_ROOT, "exec_summary_explainer.pptx")

TILES = [
    ("Delivery health",
     "RAG dot. Engine default (jira_exec_summary.compute_stats -> suggested_status): "
     "blocker present -> red 'At risk'; nothing delivered -> amber 'Watch'; else green "
     "'On track'. Manager overrides via narrative.json delivery_health{class,label}."),
    ("Epics started",
     "started | total. recent_epics.linked = PART epics whose Jira parent is Initiative "
     "AA-431 (compute_epics). 'Started' = has >=1 child ticket created."),
    ("Backlog | delivered",
     "backlog_total = OPEN tickets whose parent epic rolls up to a real Initiative "
     "(snapshot, NOT sprint-scoped -- differs from Jira's own board Backlog panel, which "
     "excludes sprinted tickets instead). backlog_delivered = tickets RESOLVED in the last "
     "30 days, any age. Orphan tickets are excluded and counted in auto_caveats."),
    ("Avg epic cycle time",
     "Days from EPIC creation -> resolution, averaged over epics resolved in the current "
     "30-day window (compute_epic_cycle_time). Sub-value = delta vs. the same average for "
     "the prior 30-day window. Discard-status epics excluded; null when 0 epics resolved."),
    ("Throughput",
     "Non-epic, non-subtask, non-Discard tickets RESOLVED in the window / (window_days/7). "
     "Delta vs. the same calculation for the prior 30-day window (compute_prior_period)."),
    ("Story pts: done | WIP | total",
     "Sum of Story Points (customfield_13078) on tickets carrying the active Sprint "
     "(customfield_10020). done = resolved tickets' points; WIP = unresolved tickets whose "
     "Jira status category is 'In Progress'; total = all points committed to the sprint. "
     "Shows '-' until a sprint is active (compute_sprint_stats)."),
    ("PRs merged (sprint) | open now",
     "GitHub PR counts via GITHUB_TOKEN/GITHUB_REPOS (github_prs.py). merged = PRs merged "
     "during the linked ticket's sprint (or last 30 days if no sprint yet); open_now = the "
     "repo's live open-PR count. PRs cross-link to Jira keys parsed from PR title/body."),
    ("Blockers",
     "Count of Jira issue links typed 'is blocked by' where the blocking ticket is not in "
     "a Done-like status (Done/Closed/Resolved/Discard/Cancelled). Scanned project-wide, "
     "not just initiative-linked tickets."),
]

LEFT_TOP = (
    "ACTIVE EPICS -- BY PRIORITY",
    [
        "Source: recent_epics.linked -- epics whose Jira parent = Initiative AA-431.",
        "Sort: priority_rank desc (Highest=5 ... Lowest=1), engine-sorted -- list order IS priority order.",
        "Each row: priority swatch+label; NEW badge = created within the 30-day window; "
        "up/down-PRI badge = priority changed within window (via Jira changelog); right "
        "side = status (Jira 'In Progress' category shown as 'In Progress') + done/total "
        "child tickets.",
    ],
)
LEFT_BOTTOM = (
    "WHAT'S NEXT",
    [
        "Manually written by the manager subagent (narrative.json -> whats_next) -- NOT auto-computed.",
        "Derived from epic sequencing/descriptions and pending decisions in data.json; "
        "honest timing only ('next sprint'), never a fabricated date.",
    ],
)
CENTER_TOP = (
    "KEY UPDATES",
    [
        "Manually written (narrative.json -> key_updates).",
        "Must be grounded in data.resolved_this_period -- tickets RESOLVED in the 30-day "
        "window, using real description text (ADF flattened via jira_report.adf_to_text).",
        "Never based on epic goals/aspirational scope, and never inflated past what the "
        "ticket supports.",
    ],
)
CENTER_BOTTOM = (
    "FOCUS AREAS",
    [
        "Manually written (narrative.json -> focus_areas).",
        "Drawn from data.blocked (issue links), data.stale (open >=14 days untouched), "
        "data.overdue (past due date) -- surfaces the worst by days_since_update/days_overdue.",
    ],
)
RIGHT_TOP = (
    "THROUGHPUT TREND",
    [
        "8-week rolling count of tickets RESOLVED per week (compute_trend), Monday-aligned "
        "buckets, most recent week last.",
        "A trend direction is only claimed by the manager if >=2 non-zero weeks exist in "
        "each half of the window.",
    ],
)
RIGHT_BOTTOM = (
    "VELOCITY -- STORY POINTS / SPRINT",
    [
        "Committed vs. completed Story Points per CLOSED sprint (last 6), from compute_sprint_stats.",
        "Stays 'Awaiting data' until at least one sprint has closed -- never a placeholder/mock number.",
    ],
)


def explain_tile(slide, x, y, w, h, label, explanation):
    tile = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    tile.fill.solid()
    tile.fill.fore_color.rgb = WHITE
    tile.line.color.rgb = TILE_BORDER
    tile.line.width = Pt(1.5)
    tile.shadow.inherit = False
    tf = _tf(tile)
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    _para(tf, label.upper(), 7.5, bold=True, color=HEADER_GREEN, first=True, space_after=1)
    _para(tf, explanation, 6.2, color=TEXT_GRAY, space_after=0)


def panel(slide, x, y, w, h, title, lines):
    hy = section_header(slide, x, y, w, title)
    bullets(slide, x, Emu(hy + Inches(0.06)), w, h, lines, size=8.5)


def build(out_path: str) -> None:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    title = slide.shapes.add_textbox(MARGIN, Inches(0.12), Inches(11), Inches(0.55))
    tf = _tf(title)
    _para(tf, "Executive Summary -- HOW EACH FIELD IS CALCULATED", 22, bold=True,
          color=HEADER_GREEN, first=True)
    sub = slide.shapes.add_textbox(MARGIN, Inches(0.62), Inches(12.7), Inches(0.35))
    tf = _tf(sub)
    _para(tf, "Reference copy of exec_summary.pptx's layout. Not real data -- each "
              "element explains its source query/computation instead. Regenerate with "
              "explain_layout.py whenever the pipeline's field definitions change.",
          9, color=TEXT_GRAY, first=True)

    # --- KPI tile row (identical geometry to format_pptx.build) ---
    n = len(TILES)
    gap = Inches(0.12)
    usable = SLIDE_W - 2 * MARGIN - (n - 1) * gap
    tw = Emu(usable // n)
    ty = Inches(1.05)
    th = Inches(0.92)
    for i, (label, explanation) in enumerate(TILES):
        tx = Emu(MARGIN + i * (tw + gap))
        explain_tile(slide, tx, ty, tw, th, label, explanation)

    # --- three body columns; each has a top slot and a bottom slot ---
    body_y = Inches(2.2)
    bottom_y = Inches(5.05)
    lx, lw = MARGIN, Inches(4.0)
    cx, cw = Inches(4.45), Inches(4.35)
    rx, rw = Inches(8.95), Inches(4.08)

    panel(slide, lx, body_y, lw, Inches(2.6), *LEFT_TOP)
    panel(slide, lx, bottom_y, lw, Inches(2.0), *LEFT_BOTTOM)
    panel(slide, cx, body_y, cw, Inches(2.6), *CENTER_TOP)
    panel(slide, cx, bottom_y, cw, Inches(2.0), *CENTER_BOTTOM)
    panel(slide, rx, body_y, rw, Inches(1.9), *RIGHT_TOP)
    panel(slide, rx, bottom_y, rw, Inches(2.0), *RIGHT_BOTTOM)

    prs.save(out_path)


def main() -> None:
    build(OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

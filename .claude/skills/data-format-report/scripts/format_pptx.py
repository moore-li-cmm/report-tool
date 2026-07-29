#!/usr/bin/env python3
"""Render the exec summary as a native, editable single-slide .pptx.

Takes data.json + narrative.json and produces a slide meant to be shared and
hand-tweaked in PowerPoint/Keynote/Google Slides before sending on. Everything
is real text boxes / shapes / a native chart, so it edits like any normal
slide. Self-contained: only python-pptx, no Jira engine or HTML template.

Layout follows the Innova "Executive Summary" format: a KPI tile row, then two
even columns — Active epics + What's-next (left) and Key updates + Focus areas
(right). Scoped to the fields PART's Jira data actually supports (no Spend /
Say-Do — the Story points KPI tile renders once a sprint exists, else a "not
tracked" placeholder, never a mock number).

Usage:
    ./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py
"""

from __future__ import annotations

import json
import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# --- palette (approximating the Innova reference) --------------------------
HEADER_GREEN = RGBColor(0x1B, 0x5E, 0x4F)   # section header bars
TILE_BORDER = RGBColor(0x53, 0x9E, 0x6B)    # KPI tile outline
VALUE_GREEN = RGBColor(0x1B, 0x5E, 0x4F)    # KPI value text
TEXT_DARK = RGBColor(0x1A, 0x1A, 0x1A)
TEXT_GRAY = RGBColor(0x59, 0x57, 0x53)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BAR_GOOD = RGBColor(0x2E, 0x8B, 0x3D)
BAR_MID = RGBColor(0xE0, 0x9B, 0x00)
BAR_LOW = RGBColor(0x2A, 0x78, 0xD6)
RANK_BADGE = RGBColor(0x8A, 0x88, 0x82)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.3)

# Repo root is four levels up (scripts/ -> data-format-report/ -> skills/ -> .claude/).
# The pipeline reads data.json + narrative.json and writes exec_summary.pptx there.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
DATA_PATH = os.path.join(_REPO_ROOT, "data.json")
NARRATIVE_PATH = os.path.join(_REPO_ROOT, "narrative.json")
OUT_PATH = os.path.join(_REPO_ROOT, "exec_summary.pptx")


def _tf(shape):
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.06)
    tf.margin_right = Inches(0.06)
    tf.margin_top = Inches(0.03)
    tf.margin_bottom = Inches(0.03)
    return tf


def _para(tf, text, size, *, bold=False, color=TEXT_DARK, align=PP_ALIGN.LEFT,
          first=False, space_after=2):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = "Calibri"
    return p


def _run(p, text, size, *, bold=False, color=TEXT_DARK, italic=False):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = "Calibri"
    return r


def section_header(slide, x, y, w, text):
    h = Inches(0.3)
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    box.fill.solid()
    box.fill.fore_color.rgb = HEADER_GREEN
    box.line.fill.background()
    box.shadow.inherit = False
    tf = _tf(box)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _para(tf, text.upper(), 10.5, bold=True, color=WHITE, first=True)
    return Emu(y + h)


def kpi_tile(slide, x, y, w, h, value, label, *, empty_dot=False, sub=None, value_size=19):
    tile = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    tile.fill.solid()
    tile.fill.fore_color.rgb = WHITE
    tile.line.color.rgb = TILE_BORDER
    tile.line.width = Pt(1.5)
    tile.shadow.inherit = False
    tf = _tf(tile)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    if empty_dot:
        # Deliberately blank: an unfilled circle for whoever presents the slide
        # to color/annotate by hand in PowerPoint. Never auto-computed — this
        # tile is a manual fill-in, not a data-driven one.
        d = Inches(0.32)
        dot = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Emu(x + (w - d) // 2), Emu(y + Inches(0.16)), d, d
        )
        dot.fill.background()
        dot.line.color.rgb = TILE_BORDER
        dot.line.width = Pt(1.5)
        dot.shadow.inherit = False
        _para(tf, "", 6, first=True)
        _para(tf, label.upper(), 8, bold=True, color=TEXT_GRAY, align=PP_ALIGN.CENTER)
    else:
        _para(tf, value, value_size, bold=True, color=VALUE_GREEN, align=PP_ALIGN.CENTER, first=True)
        _para(tf, label.upper(), 8, bold=True, color=TEXT_GRAY, align=PP_ALIGN.CENTER)
        if sub:
            _para(tf, sub, 7.5, color=TEXT_GRAY, align=PP_ALIGN.CENTER)


def epic_row(slide, x, y, w, epic, rank_pos):
    """One active-epic line: ordinal rank badge, the epic name with NEW /
    rank-change badges, and a right-aligned status (in-flight emphasized).
    Epics are pre-sorted by Jira's real Rank field upstream, so top-to-bottom
    IS the team's actual backlog order — not the (unused-default) Priority
    field, which this row deliberately doesn't show."""
    h = Inches(0.30)
    dot_w = Inches(0.26)
    right_w = Inches(1.2)

    # ordinal rank badge (#1, #2, ... = real backlog position, not a priority level)
    badge = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Emu(y + Inches(0.03)), dot_w, Inches(0.24))
    badge.fill.solid()
    badge.fill.fore_color.rgb = RANK_BADGE
    badge.line.fill.background()
    badge.shadow.inherit = False
    btf = _tf(badge)
    btf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _para(btf, f"#{rank_pos}", 7.5, bold=True, color=WHITE, align=PP_ALIGN.CENTER, first=True)

    # name + inline badges
    name_x = Emu(x + dot_w + Inches(0.06))
    name_w = Emu(w - dot_w - Inches(0.06) - right_w)
    nb = slide.shapes.add_textbox(name_x, y, name_w, h)
    tf = _tf(nb)
    tf.word_wrap = False
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.space_after = Pt(0)
    name = f"{epic.get('key', '')}: {epic.get('summary', '')}"
    _run(p, (name[:52] + "…") if len(name) > 53 else name, 8, color=TEXT_DARK)
    if epic.get("is_new"):
        _run(p, "  NEW", 7, bold=True, color=BAR_GOOD)
    rc = epic.get("rank_change")
    if rc:
        arrow = "▲" if rc.get("direction") == "raised" else "▼"
        _run(p, f"  {arrow}RANK", 7, bold=True, color=BAR_MID)

    # right: current status + child progress, in-flight emphasized
    rb = slide.shapes.add_textbox(Emu(x + w - right_w), y, right_w, h)
    tf = _tf(rb)
    tf.word_wrap = False
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    p.space_after = Pt(0)
    in_flight = epic.get("in_flight")
    total, done = epic.get("total", 0), epic.get("done", 0)
    prog = f"  {done}/{total}" if total else ""
    # Normalize the in-flight state to a plain-language label instead of the raw
    # Jira status name (e.g. "Analyzing" -> "In Progress").
    label = "In Progress" if in_flight else epic.get("status", "")
    _run(p, f"{label}{prog}", 7.5, bold=bool(in_flight),
         color=HEADER_GREEN if in_flight else TEXT_GRAY)


def bullets(slide, x, y, w, h, items, size=9.5):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = _tf(box)
    # Shrink text to fit the fixed box so a wordy panel can't overflow downward
    # behind the panel stacked below it (PowerPoint recalcs the fit on open).
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    if not items:
        _para(tf, "—", size, color=TEXT_GRAY, first=True)
        return
    for i, item in enumerate(items):
        _para(tf, f"•  {item}", size, color=TEXT_DARK, first=(i == 0), space_after=5)



def build(data: dict, narrative: dict, out_path: str) -> None:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # --- title + status line ---
    title = slide.shapes.add_textbox(MARGIN, Inches(0.12), Inches(9), Inches(0.55))
    tf = _tf(title)
    _para(tf, f"Executive Summary — {data.get('project','')}", 26, bold=True,
          color=HEADER_GREEN, first=True)
    sub = slide.shapes.add_textbox(MARGIN, Inches(0.66), Inches(12.7), Inches(0.3))
    tf = _tf(sub)
    _para(tf, f"{data.get('period_label','')}  ·  generated {data.get('generated_at','')}",
          9.5, color=TEXT_GRAY, first=True)

    # --- KPI tiles ---
    since_days = data.get("since_days", 30)
    epics = data.get("recent_epics", {}).get("linked", [])
    epics_started = sum(1 for e in epics if e.get("is_new"))
    epics_done = sum(1 for e in epics if e.get("is_done_recent"))

    ec = data.get("epic_cycle_time") or {}
    ecd = ec.get("days")
    tiles = [
        # Project health: deliberately blank — a manual fill-in the presenter
        # colors/annotates by hand in PowerPoint, never auto-computed.
        {"label": "Project health", "empty_dot": True},
        {"value": f"{epics_started} | {epics_done}", "label": "Started epics | done",
         "sub": f"Last {since_days}d"},
        {"value": f"{data.get('backlog_total',0)} | {data.get('backlog_delivered',0)}", "label": "Backlog | delivered"},
        {"value": f"{ecd if ecd is not None else 'n/a'} d", "label": "Epic cycle time"},
        {"value": f"{data.get('throughput_per_week', 0):.1f}", "label": "Throughput / wk"},
        # total_completed_points (jira_exec_summary.compute_sprint_stats) sums
        # completed points across every sprint ever run, not just the current
        # one — stays meaningful as sprints roll over. Null only when the
        # Sprint field has never been populated on this project.
        (
            {"value": str(int(data["total_completed_points"])), "label": "Story pts completed"}
            if data.get("total_completed_points") is not None
            else {"value": "—", "label": "Story points", "sub": "no sprint data yet"}
        ),
        {"value": str(len(data.get("blockers", []))), "label": "Flagged"},
    ]
    pr = data.get("pull_requests") or {}
    if pr.get("configured"):
        # Show "merged this period | open now" — the two numbers a reader can
        # reconcile against the repo (open_now matches GitHub's open count).
        # Deliberately NOT "opened", which collides with "open" (created-in-window
        # vs currently-open are different things and confuse readers).
        open_now = pr.get("open_now", 0)
        cur = pr.get("current_sprint")
        bucket = (pr.get("by_sprint") or {}).get(cur) if cur else None
        pr_tile = ({"value": f"{bucket['merged']} | {open_now}", "label": f"PRs merged ({cur}) | open now"}
                   if bucket else
                   {"value": f"{pr.get('merged_in_window', 0)} | {open_now}", "label": f"PRs merged {since_days}d | open now"})
        tiles.insert(len(tiles) - 1, pr_tile)  # just before Flagged
    n = len(tiles)
    gap = Inches(0.12)
    usable = SLIDE_W - 2 * MARGIN - (n - 1) * gap
    tw = Emu(usable // n)
    ty = Inches(1.05)
    th = Inches(0.92)
    for i, t in enumerate(tiles):
        tx = Emu(MARGIN + i * (tw + gap))
        kpi_tile(slide, tx, ty, tw, th, t.get("value"), t["label"],
                 empty_dot=t.get("empty_dot", False), sub=t.get("sub"), value_size=t.get("value_size", 19))

    # --- two body columns (throughput trend / velocity panels removed); each
    # has a top slot and a bottom slot, split evenly across the slide width ---
    body_y = Inches(2.2)
    bottom_y = Inches(5.05)  # pushed down so a wordy Key-updates block clears Focus areas
    gap = Inches(0.15)
    col_w = Emu((SLIDE_W - 2 * MARGIN - gap) // 2)
    lx, lw = MARGIN, col_w
    cx, cw = Emu(MARGIN + col_w + gap), col_w

    # LEFT: active epics (top) + what's next (bottom). Rows are in real backlog-
    # Rank order (sorted upstream, not Priority); each shows in-flight status
    # and NEW / rank-change badges.
    y = section_header(slide, lx, body_y, lw, "Active epics — by rank")
    ey = Emu(y + Inches(0.06))
    for rank_pos, e in enumerate(epics[:6], start=1):
        epic_row(slide, lx, ey, lw, e, rank_pos)
        ey = Emu(ey + Inches(0.30))
    if narrative.get("whats_next"):
        wy = section_header(slide, lx, bottom_y, lw, "What's next")
        bullets(slide, lx, Emu(wy + Inches(0.06)), lw, Inches(2.0),
                narrative["whats_next"], size=9)

    # RIGHT: key updates (top) + focus areas (directly below it)
    y = section_header(slide, cx, body_y, cw, "Key updates")
    bullets(slide, cx, Emu(y + Inches(0.06)), cw, Inches(2.4), narrative.get("key_updates", []), size=9)
    fy = section_header(slide, cx, bottom_y, cw, "Focus areas")
    bullets(slide, cx, Emu(fy + Inches(0.06)), cw, Inches(2.0), narrative.get("focus_areas", []), size=9)

    # (Data-note footnote intentionally omitted from the slide — caveats still
    # live in data.json/auto_caveats for reference.)

    prs.save(out_path)


def main() -> None:
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    with open(NARRATIVE_PATH, encoding="utf-8") as f:
        narrative = json.load(f)

    build(data, narrative, OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

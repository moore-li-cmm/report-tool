# Handoff

You now own this tool. [README.md](README.md) covers setup, running it, and what
the numbers mean — start there. This page is only the part a new owner needs:
how to change it, and what not to break.

You don't need to write Python. You change this tool by asking Claude Code for
what you want in plain English.

Written against the repo as of 2026-08-06.

---

## What it is

It turns your team's Jira data into a one-page PowerPoint status slide you can
then edit by hand.

It is **not** an app — no website, no button, no program to launch. It's a set of
instructions Claude Code follows, plus two small scripts. That's why changing it
mostly means editing instructions written in English.

Three steps:

| Step | What happens | Who does it |
|---|---|---|
| 1. **Fetch** | Pulls facts out of Jira and counts things. No opinions, no sentences. | a script |
| 2. **Write** | Reads those facts and writes the bullet points. | Claude |
| 3. **Draw** | Puts the numbers and bullets on the slide. | a script |

The split is the whole design. Steps 1 and 3 only count and draw; **every
judgment call lives in step 2.** That's what makes the slide trustworthy — every
claim traces back to a real ticket.

If you keep one rule: **don't let the counting scripts start writing sentences,
and don't let the slide drawer start deciding what a number means.**

---

## Changing it

Point Claude at the right file:

| To change… | The file |
|---|---|
| **How bullets are written** — voice, tone, what counts as honest | `.claude/agents/manager.md` |
| **What the slide looks like** — colors, tiles, layout | `.claude/skills/data-format-report/scripts/format_pptx.py` |
| **What gets counted** — which tickets, what a metric means | `.claude/skills/jira-data-fetch/scripts/jira_exec_summary.py` |
| **Which Jira project, how far back** | `.claude/skills/jira-data-fetch/scripts/fetch.py` (top of file) |
| **What Claude knows about this repo automatically** | `CLAUDE.md` |

`.claude/agents/manager.md` is the one worth reading yourself. Plain English, and
it holds real hand-written examples of good bullets as the target to match.

Requests that work as-is: *"Change the window from 30 days to 14."* / *"Add a KPI
tile for open bugs."* / *"Show 8 epics instead of 6."* / *"The Key Updates
bullets are too long — tighten the rule to one short line each."*

Three things that will save you:

1. **The writing rules are deliberately repeated in three files** —
   `manager.md`, `data-format-report/SKILL.md`, and `CLAUDE.md`. Change a rule in
   one and Claude follows whichever it read last. Say "update this rule
   everywhere it appears."
2. **Open the slide and look at it after every change.** Text boxes are
   fixed-size and shrink to fit, so too much content doesn't error — it renders
   too small to read, with no warning.
3. **If a number looks wrong, check `auto_caveats` in `data.json` first.** The
   tool writes itself a note every time it leaves something out. Most "that's
   wrong" moments are explained there.

---

## Don't undo these

Each looks like clutter. Each is there because of a real failure.

**The login check before it fetches anything.** Jira answers a bad password with
"200 OK, zero results" instead of an error, so an expired token looks exactly
like "the team did nothing" — and the tool would print a confident all-zeros "at
risk" slide for a VP. This is why it refuses to build a report when login fails.

**The "project health" circle is blank on purpose.** An empty circle for you to
color in by hand. The tool will never guess it. You're the one in the room.

**Dates are written as `[date]` in bold blue.** No ticket here has a due date, so
any specific date is something a human knows and the data doesn't. The tool names
the milestone and leaves an obvious blank rather than inventing a date.

**If GitHub breaks the report still runs; if Jira breaks it stops.** A missing PR
tile costs one tile. A wrong Jira number is a wrong statement to your leadership.

---

## Known gaps

- **No history, so no trends.** Each run is a snapshot with nothing to compare
  against — which is why the rules forbid "throughput is up" or "we're getting
  faster." Saving each run's `data.json` with a date on it would unlock real
  trends, and it's the highest-value improvement available.
- **No automated tests.** Changes are verified by running it and looking.
- **A Jira hiccup means re-running.** No retry; the fetch just stops.
- **The "overdue" list is always empty** because no ticket here has a due date.
  It's collected in `data.json` and never reaches the slide. Harmless, and it'd
  start working on its own if the team set due dates.

---

## If you're a developer

`CLAUDE.md` has the architecture, metric definitions, and data quirks; the two
`SKILL.md` files have the exact data formats. Claude Code reads them
automatically, so you can also just ask.

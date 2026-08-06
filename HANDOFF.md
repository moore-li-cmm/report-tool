# Handoff

You now own this tool. This page is everything you need to run it and change it.
You do not need to be able to write Python — the way you change this tool is by
asking Claude Code for what you want, in plain English.

To just *run* it, [README.md](README.md) is shorter and enough.

Written against the repo as of 2026-08-06.

---

## What this thing actually is

It turns your team's Jira data into a one-page PowerPoint status slide that you
can then edit by hand like any normal slide.

It is **not** an app. There's no website, no button, no program to launch. It's a
set of instructions that Claude Code follows, plus two small scripts that do the
mechanical work. That's why "changing it" mostly means editing instructions
written in English, not code.

It works in three steps:

| Step | What happens | Who does it |
|---|---|---|
| 1. **Fetch** | Pulls the facts out of Jira and counts things. No opinions, no sentences. | a script |
| 2. **Write** | Reads those facts and writes the bullet points. | Claude |
| 3. **Draw** | Puts the numbers and bullets onto the slide. | a script |

The important part is the split. Steps 1 and 3 never make judgment calls —
they only count and draw. **Every judgment call lives in step 2.** That's what
makes the slide trustworthy: every claim on it traces back to a real Jira ticket.

If you keep one rule from this page: **don't let counting logic start writing
sentences, and don't let the slide drawer start deciding what a number means.**

---

## Running it

One-time setup (about 5 minutes) is in
[README.md § Setup](README.md#setup-about-5-minutes-one-time) — that's where the
Jira credential instructions live. One thing people miss: your Jira account needs
read access to `AA-431`, which sits in a *different* Jira project than `PART`.

After that, every time you want a report, type this in Claude Code:

```
/weekly-report
```

You get `exec_summary.pptx` in the project folder. Open it, finish it by hand,
present it.

Three files get rebuilt fresh on every run — `data.json` (the facts),
`narrative.json` (the bullets), and `exec_summary.pptx` (the slide). Editing
those by hand is fine for a one-off, but the next run overwrites them. Real
changes go in the files below.

---

## Where things live

You'll rarely need to open these yourself — but when you ask Claude to change
something, this is the file to point it at.

| To change… | The file |
|---|---|
| **How the bullets are written** — voice, tone, what counts as honest | `.claude/agents/manager.md` |
| **What the slide looks like** — colors, boxes, tiles, layout | `.claude/skills/data-format-report/scripts/format_pptx.py` |
| **What gets counted** — which tickets, what a metric means | `.claude/skills/jira-data-fetch/scripts/jira_exec_summary.py` |
| **Which Jira project, how far back** | `.claude/skills/jira-data-fetch/scripts/fetch.py` (top of the file) |
| **What Claude knows about this repo automatically** | `CLAUDE.md` |

`.claude/agents/manager.md` is the one worth actually reading. It's plain
English, it's where the writing rules live, and it contains real hand-written
examples of good bullets as the target to match.

**One catch:** the writing rules are deliberately repeated in three places —
`manager.md`, `data-format-report/SKILL.md`, and `CLAUDE.md`. If you change a
rule, it has to change in all three or Claude will follow whichever it happened
to read last. Tell Claude "update this rule everywhere it appears" and it will
handle it.

---

## Making changes

Ask Claude Code in plain English. Some examples that work as-is:

- *"Change the report window from 30 days to 14 days."*
- *"Add a KPI tile showing open bugs."*
- *"The Key Updates bullets are too long — tighten the rule to one short line each."*
- *"Show 8 epics in the epic panel instead of 6."*
- *"Change the green on the slide to our brand blue."*
- *"Point this at the XYZ Jira project instead of PART."*

Two habits that will save you:

1. **Always open the slide and look at it after a change.** The text boxes are
   fixed-size and shrink text to fit, so too much content doesn't throw an
   error — it just renders too small to read. Nothing warns you.
2. **If a number looks wrong, ask Claude to check `auto_caveats` in
   `data.json` first.** The tool writes itself a note every time it leaves
   something out. Most "that number's wrong" moments are actually explained
   there.

---

## Why some numbers look "wrong" (they aren't)

These surprise everyone once. All three are deliberate.

**The backlog count won't match your Jira board.** The report only counts
tickets that ladder up to a real initiative. Loose tickets with no parent epic
are left out on purpose, so every delivery number on the slide is scoped the
same way and they all reconcile with each other. The caveats say how many were
skipped.

**"Delivered" can say 0 in a week your team clearly shipped things.** Same
reason: the work landed on an epic that doesn't roll up to the initiative. That's
the scope working correctly, not a bug. The slide should *explain* the zero
rather than hide it.

**Cycle time is measured on whole epics, not individual tickets** — a
longer-horizon number. When only one or two epics finish, a single epic swings
it hard, so don't over-read it.

---

## Things not to undo

Each of these looks like clutter. Each is there because of a real failure.

**The login check before it fetches anything.** Jira answers a bad password with
"200 OK, zero results" instead of an error. Without the explicit check, an
expired token looks exactly like "the team did nothing," and the tool would
cheerfully print an all-zeros "at risk" slide for a VP. This is why it refuses
to build a report when login fails.

**The "project health" circle is blank on purpose.** It's an empty circle for you
to color in by hand. The tool will never guess it. That call is yours, and
you're the one in the room.

**Dates are written as `[date]` in bold blue.** No Jira ticket here has a due
date, so any specific date is something a human knows and the data doesn't. The
tool names the milestone and leaves you an obvious blank to fill in rather than
inventing a date.

**Epic ordering comes from your drag-and-drop backlog order, not the Priority
field.** Priority is left at its default on every epic here, so it carries no
information at all. Don't reintroduce it.

**Finished epics disappear after one report.** An epic shows up in the report
covering the month it completes, then drops off, so the panel doesn't fill up
with old done work.

**If GitHub breaks, the report still runs. If Jira breaks, it stops.** A missing
PR tile costs you one tile; a wrong Jira number is a wrong statement to your
leadership. That imbalance is intentional.

---

## Known gaps

- **No history, so no trends.** Each run is a single snapshot with nothing to
  compare against — which is why the rules forbid saying "throughput is up" or
  "we're getting faster." Saving each run's `data.json` with a date on it would
  unlock real trends, and it's the single highest-value improvement available.
- **No automated tests.** Changes are verified by running it and looking.
- **A Jira hiccup means re-running.** There's no retry; the fetch just stops.
- **The "overdue" section is always empty** because no ticket here has a due
  date. Harmless — it'd start working on its own if the team began setting them.

---

## If you're a developer

The detail this page leaves out is in `CLAUDE.md` (architecture, metric
definitions, the data quirks) and the two `SKILL.md` files (the exact data
formats). Claude Code reads those automatically, so you can also just ask.

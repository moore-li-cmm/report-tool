# report-tool

Turns live Jira data (project `PART`, initiative `AA-431`) into a single-page,
editable executive-summary slide: `exec_summary.pptx`.

There is no app to run. The "product" is a Claude Code setup — one subagent plus
two skills — that fetches data, writes the narrative, and renders the slide.

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in Jira (and optionally GitHub) creds
```

All the pipeline needs is Python 3.9+ with `requirements.txt` installed
(`requests`, `python-dotenv`, `python-pptx`) — no code reads a `.venv`, and any
interpreter carrying those three deps runs the scripts. A venv is just the
recommended way to guarantee they're there. The docs and the `manager` subagent
spell out `./.venv/bin/python` because bare `python` doesn't exist on this
machine and a system `python3` isn't guaranteed to have the deps; if yours does,
`python3 <script>` works identically.

Then, in Claude Code:

```
/weekly-report
```

That invokes the `manager` subagent, which runs the whole pipeline and returns
the path to `exec_summary.pptx` plus a short spoken summary.

## Pipeline

Three stages, each a separate Claude Code concept:

| Stage | Where | What it does | Output |
|---|---|---|---|
| 1. Fetch | `jira-data-fetch` skill (`.claude/skills/jira-data-fetch/scripts/`) | Pure retrieval + aggregation from Jira (and GitHub, if configured). No prose. | `data.json` |
| 2. Analyze | `manager` subagent (`.claude/agents/manager.md`) | An LLM reads `data.json` and writes the narrative by hand — all judgment calls live here. | `narrative.json` |
| 3. Render | `data-format-report` skill (`.claude/skills/data-format-report/scripts/format_pptx.py`) | Merges both JSONs into a native, editable one-slide deck of real shapes/text boxes. | `exec_summary.pptx` |

`.claude/commands/weekly-report.md` is the user-facing entry point; it just
invokes the `manager` subagent.

### Running it manually

Any interpreter with the deps installed works; these examples use the venv's:

```bash
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py           # → data.json
# ...write narrative.json by hand per .claude/skills/data-format-report/SKILL.md...
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py  # → exec_summary.pptx
```

Neither script takes arguments. `PROJECT = "PART"` and `SINCE_DAYS = 30` are
constants at the top of `fetch.py`, and both scripts read/write `data.json`, `narrative.json`, `exec_summary.pptx` at the **repo
root** regardless of cwd.

There is no test suite, linter, or build step.

## Layout

```
.claude/
  commands/weekly-report.md          # slash command → manager subagent
  agents/manager.md                  # the analysis step (persona + rules)
  skills/jira-data-fetch/
    SKILL.md                         # data.json output contract
    scripts/fetch.py                 # orchestrator
    scripts/jira_report.py           # Jira Cloud data access: auth, JQL, changelog, ADF→text
    scripts/jira_exec_summary.py     # stats engine: backlog, delivery, cycle time, epic rollup, sprints
    scripts/github_prs.py            # optional GitHub PR stats
  skills/data-format-report/
    SKILL.md                         # narrative.json schema + writing rules
    scripts/format_pptx.py           # renderer
```

The fetch scripts are sibling files, not a package — they import each other by
bare name.

## Credentials

Repo-root `.env` (gitignored — never commit real values):

- `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` — required.
- `GITHUB_TOKEN`, `GITHUB_REPOS`, `GITHUB_API_URL` — optional PR metrics. Unset
  or erroring degrades to `pull_requests.configured: false` with a caveat; it
  never fails the run. Use `https://<host>/api/v3` for GitHub Enterprise.

Auth is verified by explicitly probing `/myself`, because Atlassian's search
endpoint returns 200 + empty results for a bad token.

## The slide

A KPI tile row and two even columns:

- **Tiles:** Project health (deliberately blank — a manual fill-in the presenter
  colors by hand), Started epics | done, Backlog | delivered, Epic cycle time,
  Throughput / wk, Story pts completed, PRs merged | open now (when configured),
  Flagged.
- **Left column:** Active epics — by rank (top 6, real Jira `Rank` order, with
  in-flight status, child progress, NEW and rank-change badges) + What's next.
- **Right column:** Key updates + Focus areas.

## Data definitions that surprise people

These are intentional. Don't "fix" them.

- **Backlog and delivery are initiative-scoped.** `backlog_total`,
  `backlog_delivered`, `resolved_this_period`, and `throughput_per_week`
  count an item only if its parent epic rolls up to a real
  Initiative. Orphan tickets are excluded, with an `auto_caveats` line saying how
  many. It intentionally won't match the board's raw open count, and delivered
  can read `0` even when real work shipped under a non-initiative epic — that's
  the scope working, and the report should say so plainly.
- **Cycle time is epic-level**, not per ticket (`epic_cycle_time.days`:
  creation→resolution), and scoped to initiative-connected epics. `null` when no
  initiative epic resolved in the window. Check `resolved_epics` — one epic
  swings a thin number.
- **`data.json` is a single-window snapshot.** No historical series, no
  prior-period counts, no deltas — so nothing in it supports a
  "faster/slower than last month" claim. Every number is "where things stand
  now."
- **Epics have no priority.** PART's Priority sits at an unused default on every
  epic, so ordering comes from Jira's native `Rank` field. `rank_change` carries
  only a bare `raised`/`lowered` direction — Jira never logs an absolute
  position.
- **`sprint_goal`/`total_completed_points` are computed live** from the Sprint
  and Story Points fields, and are `null` (not `0`) when there's genuinely no
  active sprint / no sprint data at all — `auto_caveats` says which. Report
  `throughput_per_week`/`epic_cycle_time` instead; never fabricate a
  story-point number.
- **Status `Discard` marks test/junk data** (e.g. `PART-1` "Sample Epic") and is
  excluded project-wide, with a caveat naming what was dropped.
- **Epics outside `AA-431`** land in `recent_epics.other` and must be reported
  separately, not blended into initiative progress.

## Narrative rules

Full guidance lives in `.claude/skills/data-format-report/SKILL.md` and
`.claude/agents/manager.md`. The short version:

- `key_updates` (impact) is grounded in what actually **resolved**, never in
  aspirational epic descriptions.
- Never inflate a bullet past its ticket. A scaffold ticket "created the repo
  scaffold"; it did not "stand up the service." A spike "researched options"; it
  did not "settle the design." Test: would the engineer who closed it recognize
  the claim as their work?
- Plain language for a skip-level reader — outcomes, not ticket keys.
- Never claim a metric went up or down — there's no baseline in the data.
  "How is the team improving" is answered from concrete process facts.
- `key_updates` and `focus_areas` share one column — ~3 short one-line bullets
  each.
- `whats_next` carries forward outlook with honest timing, never invented dates.

## Retargeting to another Jira project

Update: `PROJECT` in `fetch.py`; `INITIATIVE_KEY`/`INITIATIVE_NAME`/
`EXCLUDED_STATUSES` in `jira_exec_summary.py`; `SPRINT_FIELD_ID` in
`jira_report.py`; `STORY_POINTS_FIELD_ID`/`RANK_FIELD_ID` in
`jira_exec_summary.py`. Custom field IDs are per-instance — re-derive them via
`GET /rest/api/3/field` (Rank is usually named exactly "Rank", type
`gh-lexo-rank`).

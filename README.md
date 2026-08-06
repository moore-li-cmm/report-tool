# report-tool

Turns live Jira data (project `PART`, initiative `AA-431`) into a single-page,
editable executive-summary slide: `exec_summary.pptx`.

There is no app to run and no server to deploy. The "product" is a Claude Code
setup — one subagent plus two skills — that fetches data, writes the narrative,
and renders the slide. You run it by typing `/weekly-report` in Claude Code.

**Want to change the code?** Read [HANDOFF.md](HANDOFF.md) — it maps every file,
explains how each stage works, and lists the exact edit for common changes.

---

## Setup (about 5 minutes, one time)

### 1. Clone and install

```bash
git clone https://github.com/moore-li-cmm/report-tool.git
cd report-tool
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Requires Python 3.9 or newer. The three dependencies are `requests`,
`python-dotenv`, and `python-pptx`.

> The venv is gitignored, so a fresh clone always needs this step. Nothing in
> the code reads `.venv` — any interpreter with those three packages runs the
> scripts — but the docs use `./.venv/bin/python` because bare `python` often
> isn't on PATH and a system `python3` may not have the deps.

### 2. Get a Jira API token

1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens> (log in
   with the same Atlassian account you use for Jira).
2. Click **Create API token**.
3. Give it a label (e.g. `report-tool`) and click **Create**.
4. **Copy the token now** — Atlassian never shows it again. If you lose it,
   delete the token and create a new one.

You also need:

- **`JIRA_BASE_URL`** — your Jira site root, no trailing path. Copy it out of
  the browser when you're looking at a ticket: from
  `https://acme.atlassian.net/browse/PART-123`, the base URL is
  `https://acme.atlassian.net`.
- **`JIRA_EMAIL`** — the email address of the account that created the token.
  The token is useless without the matching email; Jira Cloud uses them together
  as basic auth.

The account needs read access to the `PART` project *and* to initiative `AA-431`,
which lives in a different project (`AA`). If `AA-431` isn't visible to you,
epic rollup comes back empty.

### 3. Get a GitHub token (optional — skip if you don't want PR metrics)

PR counts are a bonus tile on the slide. Everything else works without them.

1. Go to **GitHub → Settings → Developer settings → Personal access tokens →
   Tokens (classic)** and click **Generate new token (classic)**.
2. Tick the **`repo`** scope (read access to the repos you want counted).
3. Generate, then copy the token.

For GitHub Enterprise / internal hosts, do the same on that host and set
`GITHUB_API_URL` to `https://<host>/api/v3` instead of the public
`https://api.github.com`.

### 4. Write your `.env`

```bash
cp .env.example .env
```

Then open `.env` and fill it in:

```bash
JIRA_BASE_URL=https://acme.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=paste_the_token_from_step_2

# Optional — leave these out entirely to skip the PR tile.
GITHUB_API_URL=https://api.github.com
GITHUB_TOKEN=paste_the_token_from_step_3
GITHUB_REPOS=owner/dhc-pa-adapter
```

`GITHUB_REPOS` is a comma-separated list of `owner/repo` — e.g.
`acme/dhc-pa-adapter,acme/other-service`.

`.env` is gitignored. **Never commit real credentials.**

### 5. Check that it works

```bash
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py
```

Success looks like `Wrote /path/to/report-tool/data.json`. If credentials are
wrong you get an explicit error instead of a broken report — see
[Troubleshooting](#troubleshooting).

---

## Running the report

In Claude Code, from the repo root:

```
/weekly-report
```

That invokes the `manager` subagent, which runs all three stages and returns the
path to `exec_summary.pptx` plus a short spoken summary. Takes a minute or two —
most of it is Jira API calls.

Open `exec_summary.pptx` in PowerPoint (or Keynote / Google Slides). Everything
on it is a real text box or shape, so edit it freely before sending it on. Two
things are *meant* to be finished by hand:

- **The "Project health" tile** is an empty circle — the presenter colors it in.
- **`[date]` placeholders** (bold blue) in *What's next* — the tool never invents
  a date, because Jira carries no due dates. Fill in the real ones.

### Running the stages manually

Useful when you only want the data, or want to re-render after hand-editing the
narrative:

```bash
# 1. Fetch  →  data.json
./.venv/bin/python .claude/skills/jira-data-fetch/scripts/fetch.py

# 2. Write narrative.json yourself (this is the LLM/judgment step —
#    see .claude/skills/data-format-report/SKILL.md for the schema and rules)

# 3. Render  →  exec_summary.pptx
./.venv/bin/python .claude/skills/data-format-report/scripts/format_pptx.py
```

Neither script takes arguments, and both read/write `data.json`,
`narrative.json`, and `exec_summary.pptx` at the **repo root** regardless of
which directory you run them from.

There is no test suite, linter, or build step in this repo.

---

## What the slide contains

A KPI tile row across the top, then two even columns.

**Tiles** (left to right): Project health (blank, manual), Epics created |
completed, Epic cycle time, Backlog | completed, Throughput / wk, Story pts
completed, PRs merged | open now (only when GitHub is configured), Flagged.

**Left column:** Top 6 epics by rank (real Jira `Rank` order, with child
progress and NEW / rank-change badges), then *What's next*.

**Right column:** *Key updates*, then *Focus areas*.

Epic rows show child progress and badges but not the epic's workflow status —
`in_flight` is in `data.json`, the row just has no horizontal room for it.

---

## How it works

Three stages, each a separate Claude Code concept:

| Stage | Where | What it does | Output |
|---|---|---|---|
| 1. Fetch | `jira-data-fetch` skill (`.claude/skills/jira-data-fetch/scripts/`) | Pure retrieval + aggregation from Jira (and GitHub, if configured). No prose. | `data.json` |
| 2. Analyze | `manager` subagent (`.claude/agents/manager.md`) | An LLM reads `data.json` and writes the narrative by hand — all judgment calls live here. | `narrative.json` |
| 3. Render | `data-format-report` skill (`.claude/skills/data-format-report/scripts/format_pptx.py`) | Merges both JSONs into a native, editable one-slide deck of real shapes/text boxes. | `exec_summary.pptx` |

`.claude/commands/weekly-report.md` is the user-facing entry point; it just
invokes the `manager` subagent.

```
.claude/
  commands/weekly-report.md          # slash command → manager subagent
  agents/manager.md                  # the analysis step (persona + writing rules)
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

For a file-by-file walkthrough and instructions for modifying any of it, see
[HANDOFF.md](HANDOFF.md).

**Why an API token rather than the Atlassian MCP server.** Both can read Jira,
but MCP puts every issue through the model's context: each tool call's raw JSON
becomes tokens, and a 30-day PART pull is tens of thousands of them before any
analysis starts. `fetch.py` does the same reads over plain REST, aggregates in
Python, and hands the model one already-summarized `data.json` — a few dozen
requests, no round trip through a model per query. That's why the pipeline
carries its own Jira client instead of leaning on the MCP tools that are also
available in this repo.

---

## Data definitions

- **Backlog and delivery are initiative-scoped.** `backlog_total`,
  `backlog_delivered`, `resolved_this_period`, and `throughput_per_week` count an
  item only if its parent epic rolls up to a real Initiative. Orphan tickets are
  excluded, with an `auto_caveats` line saying how many. It intentionally won't
  match the board's raw open count, and delivered can read `0` even when real
  work shipped under a non-initiative epic — that's the scope working, and the
  report should say so plainly.
- **Cycle time is epic-level**, not per ticket (`epic_cycle_time.days`:
  creation→resolution), and scoped to initiative-connected epics. `null` when no
  initiative epic resolved in the window. Check `resolved_epics` — one epic swings
  a thin number.
- **`data.json` is a single-window snapshot.** No historical series, no
  prior-period counts, no deltas — so nothing in it supports a "faster/slower
  than last month" claim. Every number is "where things stand now."
- **Epics have no priority.** PART's Priority sits at an unused default on every
  epic, so ordering comes from Jira's native `Rank` field. `rank_change` carries
  only a bare `raised`/`lowered` direction — Jira never logs an absolute position.
- **`sprint_goal`/`total_completed_points` are computed live** from the Sprint and
  Story Points fields, and are `null` (not `0`) when there's genuinely no active
  sprint / no sprint data at all — `auto_caveats` says which. Report
  `throughput_per_week`/`epic_cycle_time` instead; never fabricate a story-point
  number.
- **Status `Discard` marks test/junk data** (e.g. `PART-1` "Sample Epic") and is
  excluded project-wide, with a caveat naming what was dropped.
- **Epics outside `AA-431`** land in `recent_epics.other` and must be reported
  separately, not blended into initiative progress.

---

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
- Never claim a metric went up or down — there's no baseline in the data. "How is
  the team improving" is answered from concrete process facts.
- Each panel has its own grammar, and the difference is load-bearing:
  `key_updates` is verb-first past tense (5-7 bullets, one line each),
  `focus_areas` is verb-less noun phrases naming investment themes (3-4 bullets,
  up to two lines), `whats_next` is either (4-6 bullets). A leading verb in
  `focus_areas` means a key update landed in the wrong box.
- `whats_next` carries forward outlook with honest timing, never invented dates —
  a specific date is always a literal `[date]` for the presenter to fill.

---

## Troubleshooting

**`ERROR: Jira auth failed (401) …`** — the token is invalid or expired, or the
email doesn't match the account that created it. Generate a fresh token at
<https://id.atlassian.com/manage-profile/security/api-tokens> and update
`JIRA_API_TOKEN`. The pipeline refuses to write a report in this case on purpose:
Atlassian's search endpoint answers a bad token with `200 {"issues": []}`, which
would otherwise render a confident, completely false "nothing delivered" slide.

**`KeyError: 'JIRA_BASE_URL'`** — no `.env` at the repo root, or the variable is
missing from it. `.env` must sit next to `README.md`, not in `.claude/`.

**Everything reads 0 but auth passed** — usually a scoping question, not a bug.
Check `auto_caveats` in `data.json`: it names how many tickets were excluded for
having no parent epic under a real Initiative. See
[Data definitions](#data-definitions-that-surprise-people).

**No PR tile on the slide** — `GITHUB_TOKEN`/`GITHUB_REPOS` aren't set, or the
fetch failed. GitHub problems never fail the run; they degrade to
`pull_requests.configured: false` with an `auto_caveats` line explaining why.
Check that line for the actual error.

**`ModuleNotFoundError: No module named 'pptx'`** — you're running a different
interpreter than the one you installed into. Use `./.venv/bin/python`.

---

## Retargeting to another Jira project

Update: `PROJECT` in `fetch.py`; `INITIATIVE_KEY`/`INITIATIVE_NAME`/
`EXCLUDED_STATUSES` in `jira_exec_summary.py`; `SPRINT_FIELD_ID` in
`jira_report.py`; `STORY_POINTS_FIELD_ID`/`RANK_FIELD_ID` in
`jira_exec_summary.py`. Custom field IDs are per-instance — re-derive them via
`GET /rest/api/3/field` (Rank is usually named exactly "Rank", type
`gh-lexo-rank`). Step-by-step in
[HANDOFF.md](HANDOFF.md#11-retargeting-to-a-different-jira-project).

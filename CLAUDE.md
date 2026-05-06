# claude-transcript-analyzer

## Project purpose

A tool that parses Claude Code transcripts (`.jsonl`) and **automatically collects, aggregates, and visualizes Skills and Subagents usage**.

Claude Code Hooks collect events in real time and append them to `data/usage.jsonl`.
A browser dashboard then renders the data.

## Data flow

```
Claude Code activity
  │
  │  ── Tool / Skill events ─────────────────────────────
  │  PostToolUse(Skill)               →  hooks/record_skill.py
  │  PostToolUseFailure(Skill)        →  hooks/record_skill.py
  │  UserPromptSubmit                 →  hooks/record_skill.py
  │  UserPromptExpansion              →  hooks/record_skill.py   (primary path for slash-command observation)
  │  PostToolUse(Task|Agent)          →  hooks/record_subagent.py
  │  PostToolUseFailure(Task|Agent)   →  hooks/record_subagent.py
  │  SubagentStart / SubagentStop     →  hooks/record_subagent.py
  │
  │  ── Session / Context events ──────────────────
  │  SessionStart / SessionEnd        →  hooks/record_session.py
  │  PreCompact / PostCompact         →  hooks/record_session.py
  │  Notification                     →  hooks/record_session.py
  │  InstructionsLoaded               →  hooks/record_session.py
  │
  │  ── Integrity check ─────────────────────────────
  │  Stop                             →  hooks/verify_session.py  (transcript ↔ usage reconciliation)
  │
  │  ── Dashboard auto-launch ───────────────────────
  │  SessionStart                     →  hooks/launch_dashboard.py  (idempotent launcher)
  │  UserPromptExpansion              →  hooks/launch_dashboard.py
  │  UserPromptSubmit                 →  hooks/launch_dashboard.py
  │  PostToolUse                      →  hooks/launch_dashboard.py
  │
  │  ── Archive auto-launch (Issue #30) ──────────────────
  │  SessionStart                     →  hooks/launch_archive.py    (idempotent launcher)
  ↓
data/usage.jsonl          ← append-only event log (hot tier / last 180 days)
  │  ├ hooks/_append.py          ← locked append (blocking SH / cross-platform)
  │  └ scripts/archive_usage.py  ← moves events older than 180 days into monthly .jsonl.gz (gzip)
  │       ↓
  │   archive/YYYY-MM.jsonl.gz   ← cold tier / immutable / read by opt-in readers
  │
  ├── reports/summary.py     →  terminal aggregate report (--include-archive includes archive)
  ├── reports/export_html.py →  standalone HTML report (--include-archive same as above)
  └── dashboard/server.py    →  browser dashboard (hot tier only — fixed at 180 days by spec)
```

The actual storage location is `~/.claude/transcript-analyzer/usage.jsonl` (a path that survives plugin updates).
For tests, the `USAGE_JSONL` / `HEALTH_ALERTS_JSONL` / `ARCHIVE_DIR` /
`ARCHIVE_STATE_FILE` / `USAGE_JSONL_LOCK` environment variables can override these paths.

## Docs — spec / reference

Detailed docs are split by topic.

Sorting criteria: "violation causes a **bug**" → spec (`docs/spec/`); "ignorance leads you to **step on a landmine**" → reference (`docs/reference/`).
The README in each directory contains the detailed sorting policy.

## File layout

```
claude-transcript-analyzer/
├── .claude-plugin/
│   ├── plugin.json           # plugin metadata
│   └── marketplace.json      # marketplace metadata
├── hooks/                    # plugin hook definitions
├── commands/                 # slash-command definitions
├── dashboard/                # local HTTP dashboard server
├── reports/                  # report-generation scripts
├── subagent_metrics.py       # shared subagent aggregation logic (invocation-level pairing)
├── scripts/                  # utilities for manual / batch execution
├── data/                     # data placeholder (unused as of v0.7.3)
├── tests/
└── docs/
    ├── transcript-format.md  # raw transcript format + Hook input schema + Archive evolution rules
    ├── spec/                 # current spec (contract) — see README.md for sorting criteria
    │   └── legacy/           # v0.1-era direct parse procedures (historical archive)
    ├── reference/            # design decisions, gotchas, patterns — see README.md for sorting criteria
    ├── plans/
    │   └── archive/          # completed plans (historical archive)
    └── review/resolved/      # resolved review notes
```

> When the plugin is active, the archive output path is `~/.claude/transcript-analyzer/archive/YYYY-MM.jsonl.gz`,
> and the state marker is `~/.claude/transcript-analyzer/.archive_state.json`.

## Development conventions

- Implement using **TDD** (write tests first)
- **No external libraries** (stdlib only)
- Test isolation: `USAGE_JSONL` overrides `DATA_FILE`; `HEALTH_ALERTS_JSONL` overrides `ALERTS_FILE` in `verify_session.py`; `ARCHIVE_DIR` / `ARCHIVE_STATE_FILE` / `USAGE_JSONL_LOCK` cover archive concerns; `USAGE_RETENTION_DAYS` overrides retention
- Built-in commands are not recorded: `/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`

## Dashboard design discipline

### Drafting viz / data plans

Before drafting a viz / aggregation panel plan, run a field-distribution probe against real `usage.jsonl`. Quote the histogram observation (e.g. `<missing>: 202 / expansion: 75 / submit: 0`) in the plan's Observations / Risk section.

- If a single bucket of any one field exceeds **>70%**, list it as a Risk row for signal-death and propose a schema-level mitigation (e.g. splitting into a dedicated column)
- Following AC literals and user value are different things — the AC in an Issue body is the authoring-time assumption, not a guarantee of real data shape
- When examples in the Issue body (`memory_type: "user / project / skill"`) differ from real data (`"User" / "Project"`) in case/form, choose **verbatim passthrough** and do not normalize in the aggregator (don't hide real-world data quirks)

### Dashboard panel authoring discipline

In this codebase, **help text** = the `?`-button popup attached to each panel — concretely, the `<span class="help-pop">…</span>` blocks inside `dashboard/template/shell.html`, opened by `dashboard/template/scripts/80_help_popup.js`. Users read these popups as the authoritative description of what the panel computes (which hook events, which filters, which formula).

Help text is therefore a **claim** about the implementation and spec, not a free-form description. New panels go through 4-axis verification:

1. **Spec match**: verify enum values / field names / filter conditions against the official hook / API documentation by verbatim string match
2. **Data smoke**: assert at least one fixture per enum value enumerated in the spec, and assert non-empty
3. **Live data smoke**: confirm at least one of the categories the panel claims has a non-trivial count in real `usage.jsonl`
4. **Help text vs impl**: re-read help text with fresh eyes after implementation and check "is this still true?"

"**0 events forever**" is a bug suspect, not "the feature is unused." Panels with zero counts for 30+ days are flag candidates. When auditing existing panels, perform pairwise comparisons in the order: help-text claim → code filter conditions → upstream spec.

### API / UI naming separation

API field names use stable technical English (e.g. `autonomy_rate`); UI display text iterates freely (e.g. `LLM率`). In spec docs:

- API field shape: fenced JSON code block (= stable contract)
- UI label: surrounding prose (= flex-with-iteration; e.g. "in the UI, the `autonomy_rate` column is shown as 『LLM率』")

When asked to perform a UI rename, the grep scope is: template HTML / template tests / spec doc prose / external docs (release notes). Do not touch the aggregator / API / JSON schema / aggregator tests. Localized field names (e.g. putting `LLM率` into the JSON contract) become painful when localization expands later.

## Branching workflow

Operate in the **release branch model**.

```
main
  └─ vX.Y.Z              ← release branch (per-release integration point)
       ├─ feature/<issue-number>-<slug>  → PR → merged into vX.Y.Z
       └─ ...             (when release is ready, open release PR vX.Y.Z → main)
```

- At the start of a new release cycle, branch `vX.Y.Z` from `main` and push to remote
- Individual features branch off `vX.Y.Z` as `feature/<issue-number>-<slug>` — feature PRs target `vX.Y.Z` (not main)
- When the release is ready, open a release PR from `vX.Y.Z` → `main` (see the `patch-release` skill for details)

For detailed recipes (base discovery, coordination-branch idempotency, branch protection preset, required-context gotchas), see `docs/reference/branching-workflow.md`.

## Common commands

```bash
# Run tests
python3 -m pytest tests/
```

## Transcript source files

The Claude Code transcripts that this tool processes live under `~/.claude/projects/`.
See `docs/transcript-format.md` for details.

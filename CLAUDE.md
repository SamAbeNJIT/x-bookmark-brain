# x-bookmark-brain

Personal local web app that backfills, auto-categorizes, and AI-searches X (Twitter)
bookmarks. See `README.md` for the overview and `docs/PRD.md` for the full spec.

## Agent skills

### Issue tracker

Issues and PRDs live as **GitHub issues** in this repo (external PRs are *not* a triage
surface). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo: `CONTEXT.md` + `docs/adr/` at the root. See `docs/agents/domain.md`.

## Project conventions

- Python + FastAPI backend, local SQLite + a local vector index. AI runs on Amazon Bedrock.
- Use the domain vocabulary in `CONTEXT.md` for code, issues, and tests.
- Keep the two architectural seams thin and well-defined: the **X ingestion client** and the
  **Bedrock AI client** (see `docs/PRD.md` → Testing Decisions). Test against these seams.

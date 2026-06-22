# x-bookmark-brain

A personal, single-user, locally-run web app that backfills your entire **X (Twitter)
bookmark history**, auto-categorizes it, and lets you find anything via semantic search or
an AI "ask" mode that cites the saved posts.

## What it does (MVP)

- **Backfill** your full X bookmark history into a local database, in one shot, using your
  own logged-in X session (no paid API).
- **Auto-categorize** every saved post into a taxonomy *derived from your own bookmarks*
  that you approve/edit — each post can hold multiple labels.
- **Find anything** two ways: a semantic search box, and an AI **"ask"** mode that answers
  in plain language and cites the posts it used.
- **Browse by category** to rediscover things you forgot you saved.

AI runs on **Amazon Bedrock** (Claude + Titan embeddings). App runs locally for the MVP.

See [`docs/PRD.md`](docs/PRD.md) for the full product spec: problem, solution, user
stories, implementation & testing decisions, and what's explicitly out of scope (browser
extension, third-party replies, media analysis, knowledge graph, full AWS hosting).

## Status

PRD complete. Not yet implemented. Highest-risk item to de-risk first: confirming X's
internal bookmarks endpoint can be paged with a session token (see "Further Notes" in the
PRD).

## Getting started (planned)

1. Copy `.env.example` → `.env` and fill in your X session token + AWS settings.
2. Confirm **Amazon Bedrock model access** (Claude + Titan embeddings) in your region.
3. Run the backfill, review the proposed taxonomy, then search/ask.

(Build steps land here once the first issues are implemented.)

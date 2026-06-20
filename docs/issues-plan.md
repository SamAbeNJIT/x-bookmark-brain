# MVP issue breakdown (tracer-bullet vertical slices)

Derived from `docs/PRD.md` via the `to-issues` approach. Each slice is a thin end-to-end
path. Published to GitHub Issues with the `ready-for-agent` label. Granularity is a first
pass — split/merge as needed.

| # | Slice | Blocked by |
|---|-------|------------|
| 1 | Foundation: project skeleton runs, config loads, SQLite schema + vector index init | none |
| 2 | Ingest one bookmark end-to-end (session token → fetch first page → parse one → persist → count) | 1 |
| 3 | Full backfill: pagination + idempotent upsert + context capture (parent/quote/self-thread) | 2 |
| 4 | Semantic search end-to-end (embed posts via Bedrock → vector store → search box returns ranked posts) | 2 |
| 5 | Taxonomy derivation + review UI (LLM proposes taxonomy → user approves/edits) | 2 |
| 6 | Multi-label assignment + browse-by-category | 5 |
| 7 | AI "ask" mode (RAG): retrieve → answer with citations in the UI | 4 |

Roadmap (not issued): browser extension, third-party replies, media/vision, knowledge
graph, full AWS hosting + multi-user. See PRD → Out of Scope.

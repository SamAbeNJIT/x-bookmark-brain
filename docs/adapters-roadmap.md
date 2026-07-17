# Source adapters and roadmap

Phase 2 introduces one source-independent ingestion seam in `xbb.sources`:

- `SourceAdapter`: stable `name`, `is_connected`, namespaced `record_id`, and idempotent
  `backfill(con, cfg, incremental, max_total)`.
- `OAuthSourceAdapter`: adds adapter-specific configuration, authorization URL creation, and
  callback handling.
- `REGISTRY`: the runtime adapter registry used by Connect/Sync UI, generic jobs, routes, and CLI.
- Shared token storage: provider responses are encrypted under `<source>_oauth` in tenant-scoped
  `sync_state`, with tenant-bound KMS context where KMS is configured.

Adapters normalize records before the unchanged embed → categorize → feed/search/Ask/graph
pipeline. Provider IDs must be namespaced (`reddit-t3_…`, `reddit-t1_…`, `gh-…`) so they cannot
collide with X IDs or `web-…` browser IDs. Backfills page newest-first, upsert idempotently, assign
bookmark rank, and may stop after an all-already-synced page in incremental mode.

Only X is metered. Every non-X adapter is unlimited/free and must never inspect, consume, or
shrink the purchased X imports pool. Reddit still has the provider's practical ~1,000-item saved
listing cap; that API limitation is not a billing limit.

## Roadmap only — not built in Phase 2

### RSS

RSS would behave like the non-OAuth browser adapter: users store feed URLs in `sync_state`; the
adapter maps each `<item>` into the common record and uses `rss-<sha1(guid|link)>` as its ID with
`source="rss"`. Poll cursors and conditional-fetch metadata remain adapter-owned.

### Email newsletters

Newsletters can arrive through a forwarding address or IMAP connector. The adapter maps subject
to title, body to text, sender to author, and Message-ID to `newsletter-<id>`. The only new
infrastructure is the inbound-mail path; normalization and all downstream behavior stay behind
the adapter seam.

RSS and email newsletters are explicitly roadmap-only and have no Phase 2 routes, jobs, or
runtime implementations.

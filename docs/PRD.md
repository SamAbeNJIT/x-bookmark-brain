# PRD: x-bookmark-brain

> Personal tool to ingest, auto-categorize, and AI-search my X (Twitter) bookmarks.
> Status: draft for review · Triage: `ready-for-agent`

## Problem Statement

I save (bookmark) a large and growing number of posts and comments on X, but I never
organize them. When I later want to find a specific thing I saved, I'm stuck relying on
memory or X's keyword search — neither of which gets me to the right post. The backlog is
effectively a write-only pile: I put things in, but I can't reliably get them back out.
The saved posts also clearly relate to each other in ways I can't see, and that latent
structure is wasted.

## Solution

A personal, single-user, locally-run web app that:

1. **Backfills my entire X bookmark history** into a local database in one shot.
2. **Auto-categorizes** every saved post into a taxonomy the app *discovers from my own
   bookmarks* and that I then approve/edit — with each post allowed in multiple categories.
3. Lets me **find anything two ways**: a smart (semantic) search box that returns matching
   posts, and an **AI "ask" mode** that answers a plain-language question and cites the
   saved posts it used.
4. Lets me **browse by category** to rediscover things I'd forgotten I saved.

The win condition: I type a plain-language query and reliably pull up a post that X's
keyword search never could.

### Phase 2: shared-source library

Reddit saved posts/comments and GitHub starred repositories now enter the same normalization,
embedding, taxonomy, feed, search, Ask, and graph pipeline as X and browser bookmarks. Source
adapters namespace provider IDs and use tenant-bound OAuth tokens. Only X is metered; browser,
Reddit, GitHub, and future non-X adapters are unlimited and free. RSS and email newsletters are
documented adapter-roadmap items, not Phase 2 runtime features.

## User Stories

1. As a user, I want to run a one-time backfill that pulls my full X bookmark history, so that my whole backlog is captured without manual copy-paste.
2. As a user, I want the backfill to authenticate using my own logged-in X session, so that I don't need a paid X API plan or per-user OAuth.
3. As a user, I want the backfill to paginate through *all* my bookmarks, so that nothing old is silently dropped.
4. As a user, I want each saved post stored with its full text, author, timestamp, URL, and media URLs, so that categorization and search have rich context.
5. As a user, I want the original raw API payload for each post kept alongside the parsed fields, so that I never have to re-scrape if I later want a field I didn't model.
6. As a user, when a bookmarked item is a reply or a quote tweet, I want the immediate parent (or quoted) post captured too, so that short replies like "exactly right" still carry meaning.
7. As a user, when a bookmarked item is an original post, I want the author's own self-thread captured, so that the full argument is preserved.
8. As a user, I want third-party replies and media/vision analysis explicitly left out for now, so that ingestion stays fast and the index stays high-signal.
9. As a user, I want the backfill to be idempotent (re-runnable), so that running it again pulls only new bookmarks without creating duplicates.
10. As a user, I want my X session token supplied through local configuration (not hard-coded), so that I can rotate it and keep it out of the codebase.
11. As a user, I want the app to derive a starter taxonomy by reading my whole corpus, so that categories reflect what I actually save rather than a guess I made up front.
12. As a user, I want to review, rename, merge, and delete the proposed categories, so that the taxonomy matches how *I* think.
13. As a user, I want each post auto-assigned to one or more categories, so that posts about several things aren't forced into a single bucket.
14. As a user, I want the app to periodically re-derive the taxonomy and suggest new categories, so that genuinely new themes surface as my bookmarks grow.
15. As a user, I want new bookmarks (from a re-run) auto-categorized against the existing taxonomy, so that categorization stays current without manual sorting.
16. As a user, I want a semantic search box, so that I can find posts by meaning, not just exact keywords.
17. As a user, I want search results ranked by relevance with the post text, author, and a link, so that I can recognize and open the right one quickly.
18. As a user, I want an AI "ask" mode where I ask a question in plain language, so that I can get a synthesized answer instead of reading through many posts.
19. As a user, I want the AI answer to cite and link the specific saved posts it used, so that I can trust and verify it.
20. As a user, I want to browse my bookmarks by category, so that I can rediscover things I forgot I saved.
21. As a user, I want to use the whole thing as a local web app in my browser, so that I get a visual, card-based experience without hosting anything.
22. As a user, I want the AI features (categorization, embeddings, answers) to run through Amazon Bedrock, so that I stay within AWS and get high-quality Claude output.
23. As a user, I want bulk categorization to use a cheap, fast model and harder reasoning (taxonomy derivation, answers) to use a stronger model, so that the one-time backfill cost stays in cents.
24. As a user, I want everything to run on my own machine for the MVP, so that I don't have to stand up cloud hosting for a solo tool.
25. As a user, I want a clear path to productize later (multi-user, hosted), so that today's single-user choices don't require a rewrite.

## Implementation Decisions

- **Audience & scope:** Single-user, no authentication, runs locally. Data model is kept
  multi-user-friendly (stable per-post identity, no assumptions that block adding a `user`
  dimension later), but no auth/multi-tenancy is built now.
- **Ingestion:** A backfill module authenticates with the user's own X session credentials
  (`auth_token` + `ct0`, supplied via local `.env`) and pages through X's internal GraphQL
  Bookmarks endpoint. Idempotent upsert keyed on post ID. Browser-extension live-capture is
  explicitly roadmap, not MVP.
- **Captured data (per post):** parsed fields (id, canonical URL, full text, language,
  `created_at`, `bookmarked_at`, author handle/name/id, media URLs + alt-text, hashtags,
  linked URLs, like/repost counts) **plus** the original raw JSON payload retained verbatim.
  Context capture: immediate parent for replies, quoted post for quotes, and the author's
  self-thread for original posts. Third-party replies and media/vision are out of scope.
- **Categorization:** Hybrid, multi-label. (1) One-time LLM topic-derivation pass over the
  corpus proposes a starter taxonomy (~10–25 categories, each with a one-line definition);
  (2) user approves/edits/merges; (3) each post gets multiple labels assigned against that
  taxonomy at ingest; (4) periodic re-derivation suggests new categories. Categories and
  the post↔category assignments are first-class persisted entities.
- **Search:** Semantic search over post embeddings (vector similarity), returning ranked
  posts. The AI "ask" mode is RAG: retrieve relevant posts by embedding similarity, pass
  them to Claude, and return a synthesized answer with citations/links back to the source
  posts. Both ship in the MVP.
- **AI provider:** Amazon Bedrock for all AI. Claude on Bedrock for taxonomy derivation,
  label assignment, and answer synthesis (cheap/fast model for bulk per-post labeling,
  stronger model for derivation and answers). Bedrock embeddings (e.g. Amazon Titan Text
  Embeddings) for the vector index. Requires AWS credentials + Bedrock model access.
- **Persistence:** Local single-file store (SQLite) for posts, authors, categories, and
  assignments; a local vector index (e.g. sqlite-vec / LanceDB) for embeddings — zero-ops,
  single-machine. Raw JSON retained per post.
- **Application shape:** Local web app — a small Python (FastAPI) backend serving a simple
  UI (server-rendered) with three surfaces: search, ask, and browse-by-category, plus a
  taxonomy-review screen. Backfill is a CLI/admin action that the app can also trigger.
- **Hosting trajectory:** Local for MVP; full AWS hosting + multi-user is a later phase and
  treated as a deployment/extension step, not a redesign.

## Testing Decisions

- **Good tests** assert external behavior, not implementation details — e.g. "given a saved
  reply with a known parent, the stored record exposes the parent text," not "function X
  calls function Y."
- **Proposed seams (to confirm):**
  - **Ingestion seam:** wrap the X GraphQL client behind one interface; tests feed recorded
    sample payloads (including a reply, a quote, and a self-thread) and assert the parsed +
    raw records that come out. This avoids hitting X in tests and pins the parsing contract.
  - **AI seam:** wrap Bedrock (categorization, embeddings, answer synthesis) behind one
    interface so tests can substitute deterministic stand-ins and assert taxonomy-assignment
    and retrieval behavior without live API calls.
  - **Retrieval/answer seam:** test that semantic search returns the expected post for a
    known query over a small fixed fixture corpus, and that "ask" answers cite only posts
    that were actually retrieved.
  Prefer these two-to-three seams over scattering mocks; the ideal is the fewest, highest
  seams possible.

## Out of Scope (Roadmap)

- Browser extension for live capture of new bookmarks.
- Third-party (non-author) reply capture, with a hard top-K cap.
- Media/vision analysis (image OCR, video understanding) beyond storing URLs + alt-text.
- Knowledge graph of relationships between saved posts.
- Full AWS hosting, multi-user accounts, and authentication.
- Official X API (paid) ingestion path.

## Further Notes

- The ingestion approach uses the user's own session against X's internal endpoint: it's a
  ToS gray area (own data, personal use) and can break if X changes the endpoint — accepted
  for the MVP; the retained raw JSON and the single ingestion seam limit the blast radius of
  such breakage.
- Single sharpest success signal: finding a post via plain-language search that X keyword
  search could not surface.

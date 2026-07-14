# Domain glossary (ubiquitous language)

The shared vocabulary for x-bookmark-brain. Use these exact terms in code, issues, and tests.

- **Bookmark** — a post the user saved on X. The unit of ingestion. May be an original post,
  a reply, or a quote tweet.
- **Web bookmark** — a browser bookmark (URL + title + folder path) imported from a
  Chrome/Firefox Netscape-HTML export. Stored as a post with `source='browser'`, no author.
- **Source** — where a post came from: `x` (synced from X) or `browser` (uploaded export).
  Entitlement/cap math is always scoped to `x`; web bookmarks are free and metered separately.
- **Post** — any X status: the bookmarked item itself or a captured context post.
- **Author** — the X account that wrote a post (handle, display name, id).
- **Parent post** — the post directly above a bookmarked *reply* (captured for context).
- **Quoted post** — the post embedded by a bookmarked *quote tweet* (captured for context).
- **Self-thread** — a chain where the author replies to themselves to continue a point.
  Captured in full for an original bookmarked post. (Third-party replies are out of scope.)
- **Backfill** — the one-time ingestion of the user's full bookmark history via their own X
  session credentials. Idempotent: re-running pulls only new bookmarks.
- **Raw payload** — the original JSON returned by X for a post, retained verbatim alongside
  parsed fields so we never have to re-scrape for an un-modeled field.
- **Taxonomy** — the set of **categories** used to organize bookmarks. Derived once from the
  corpus by an LLM, then approved/edited by the user; periodically re-derived.
- **Category** — one labeled bucket in the taxonomy (name + one-line definition).
- **Assignment** — a (bookmark ↔ category) link. Multi-label: a bookmark may have several.
- **Embedding** — the vector representation of a post used for semantic search.
- **Semantic search** — finding bookmarks by meaning (vector similarity), not keywords.
- **Ask mode** — RAG: retrieve relevant bookmarks, synthesize an answer with **citations**
  back to the source posts.
- **Bedrock** — Amazon Bedrock, the AWS service hosting the Claude models (labeling,
  reasoning, answers) and the embedding model.

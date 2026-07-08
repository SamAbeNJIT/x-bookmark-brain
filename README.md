# x-bookmark-brain

A personal, single-user, **local** web app that pulls your entire X (Twitter) bookmark
history, auto-organizes it with AI, and lets you actually find things again — by meaning,
by question, or by browsing a color-coded map of your interests.

Everything runs on your machine. Your bookmarks, the search index, and the categories all
live in a local SQLite file; the only thing that leaves your computer is a search query or
a question (sent to Amazon Bedrock) — never your library in bulk.

## Features

- **One-shot backfill** of your full bookmark history via X's **official OAuth API** — you
  click "Connect X" and authorize (no password, no cookies). Idempotent and incremental:
  re-syncs pull only genuinely-new bookmarks (24h-deduplicated, so it's cheap).
- **Hybrid search** — find posts by meaning *and* by exact keywords (handles, URLs, acronyms).
  A pgvector cosine leg and a Postgres full-text leg are fused with Reciprocal Rank Fusion,
  entirely in the database.
- **Ask (RAG)** — ask a plain-language question; Claude answers and cites the saved posts it
  used (citations are constrained to what was actually retrieved).
- **Auto-categorization** — a taxonomy *derived from your own bookmarks* (not a canned
  list), with every post multi-labeled against it. Review / rename / merge on the Taxonomy
  page.
- **Color-coded browse** — a two-level category tree and a tinted, masonry **Feed** with a
  clickable legend to filter to one topic. Cards show author avatars and inline tweet media.
- **In-app Sync** — a button that pulls + embeds + labels anything new, in the background.

AI runs on **Amazon Bedrock**: Titan Text Embeddings for vectors, a cheap Claude (Haiku) for
bulk labeling, and a stronger Claude (Sonnet) for taxonomy derivation and answers.

## Quickstart

### Prerequisites
- Python ≥ 3.11
- A **Postgres database with pgvector** — [Neon](https://neon.com) is the easy path (create a
  project, copy the connection string). The free tier (0.5 GB) fits a ~16k-bookmark corpus.
- An AWS account with **Amazon Bedrock model access** enabled (Claude + Titan embeddings) in
  your region. Credentials come from the standard AWS chain (`~/.aws/credentials`, env vars,
  SSO, or an IAM role) — no keys live in this repo.
- An **X developer app** (free, pay-per-use): at developer.x.com create a **Native App**
  (public client / PKCE — no secret), permission **Read**, callback
  `http://127.0.0.1:8000/oauth/callback`. Copy its **OAuth 2.0 Client ID**. Bookmarks reads are
  ~$0.001/resource on pay-per-use; add a little credit.

### Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then fill it in
```

`.env` (gitignored) — see `.env.example` for the full list:
```
DATABASE_URL=postgresql://USER:PASSWORD@HOST.neon.tech/neondb?sslmode=require  # Neon + pgvector
X_CLIENT_ID=...             # OAuth 2.0 Client ID from your X developer app
X_REDIRECT_URI=http://127.0.0.1:8000/oauth/callback
AWS_REGION=us-east-1
BEDROCK_EMBEDDING_MODEL=amazon.titan-embed-text-v2:0
BEDROCK_LABELING_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0   # cheap, bulk labeling
BEDROCK_REASONING_MODEL=us.anthropic.claude-sonnet-4-6               # taxonomy + answers
```
> Current Claude models on Bedrock require the cross-region **inference profile** id
> (`us.anthropic.…`), not the bare `anthropic.…` id.

### Run the app & connect
```bash
./run.sh                    # serves http://127.0.0.1:8000  (Ctrl-C to stop)
```
Open the app → click **Connect X** → authorize. Then **Sync** pulls your bookmarks via the
official API and (with Bedrock) embeds + labels them. Data persists in your Postgres database,
so you can stop/start freely. The CLI mirrors the steps once connected:
```bash
python -m xbb backfill      # pull new bookmarks via the OAuth API (after Connect X)
python -m xbb index         # embed them          (resumable)
python -m xbb categorize    # derive taxonomy + label everything   (resumable)
```

## The app

| Page | What it does |
|---|---|
| **Search** | semantic search box → ranked cards |
| **Ask** | plain-language question → answer with citations |
| **Categories** | two-level topic tree + color legend |
| **Feed** | color-tinted masonry stream; filter by topic; infinite scroll |
| **Taxonomy** | review / rename / merge / delete categories; re-derive |
| **Sync** | pull + embed + label new bookmarks (background) |

## How it works

Two thin, well-defined seams keep external systems mockable (and the tests fast and
network-free):

- **X ingestion** (`xbb.xapi`) — OAuth 2.0 PKCE (`xbb.xauth`) + the official
  `GET /2/users/{id}/bookmarks` API; `parse_bookmark_v2` maps the response to a generic record.
  The credential never touches the server beyond the user's own token.
- **AI / Bedrock** (`xbb.ai`) — wraps embeddings, labeling, and answers behind one interface;
  tests substitute a deterministic fake.

Storage is Postgres (`xbb.storage`): posts, authors, a discovered taxonomy, multi-label
assignments, and embedding vectors (pgvector `vector(1024)` with an HNSW cosine index). Search
is a `vector <=> query` nearest-neighbour query — the database does the ranking. The schema is
multi-tenant from the ground up: every table carries a `tenant_id` (defaulted from a session
variable) with Row-Level Security policies, so it's ready for hosted/multi-user without a
rewrite. Locally you're the single default tenant.

## Commands

| Command | Purpose |
|---|---|
| `python -m xbb backfill` | pull new bookmarks via the OAuth API (after Connect X) |
| `python -m xbb index` | embed un-embedded posts |
| `python -m xbb categorize` | derive taxonomy (first run) + label unlabeled posts |
| `./run.sh [--reload]` | start the web app |
| `pytest -q` | run the test suite |

## Cost

The one-time build (embeddings + labeling over your whole history) runs in the low tens of
dollars at most — almost all of it the bulk Haiku labeling. Day-to-day use is ~free: browse
is fully local, search spends one tiny query embedding, and Ask is a cent or two per
question.

## Privacy

Single-user. Your bookmarks and index live in your own Postgres database (e.g. your Neon
project). Beyond that, the only outbound data is a search query or a question (to Bedrock),
plus the handful of posts retrieved to ground an answer — never your library in bulk.

## More

See [`docs/PRD.md`](docs/PRD.md) for the full product spec — problem, solution, user stories,
implementation & testing decisions, and what's explicitly out of scope (browser-extension
live capture, third-party reply capture, media/vision analysis, a knowledge graph, and
multi-user/hosted deployment).

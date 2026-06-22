# x-bookmark-brain

A personal, single-user, **local** web app that pulls your entire X (Twitter) bookmark
history, auto-organizes it with AI, and lets you actually find things again — by meaning,
by question, or by browsing a color-coded map of your interests.

Everything runs on your machine. Your bookmarks, the search index, and the categories all
live in a local SQLite file; the only thing that leaves your computer is a search query or
a question (sent to Amazon Bedrock) — never your library in bulk.

## Features

- **One-shot backfill** of your full bookmark history via your own logged-in X session
  (no paid API). Idempotent and resumable — re-run to pull new bookmarks, or `--resume` to
  continue past an X rate limit (it checkpoints its cursor after every page).
- **Semantic search** — find posts by meaning, not keywords. Vectors live locally; ranking
  is an exact numpy cosine over the whole corpus (instant).
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
- An AWS account with **Amazon Bedrock model access** enabled (Claude + Titan embeddings) in
  your region. Credentials come from the standard AWS chain (`~/.aws/credentials`, env vars,
  SSO, or an IAM role) — no keys live in this repo.
- Your X session cookies (`auth_token` + `ct0`) from a logged-in browser.

### Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then fill it in
```

`.env` (gitignored) — see `.env.example` for the full list:
```
X_AUTH_TOKEN=...            # the auth_token cookie
X_CSRF_TOKEN=...            # the ct0 cookie
X_BOOKMARKS_QUERY_ID=...    # the hash in the /Bookmarks request URL (DevTools > Network)
AWS_REGION=us-east-1
BEDROCK_EMBEDDING_MODEL=amazon.titan-embed-text-v2:0
BEDROCK_LABELING_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0   # cheap, bulk labeling
BEDROCK_REASONING_MODEL=us.anthropic.claude-sonnet-4-6               # taxonomy + answers
```
> Current Claude models on Bedrock require the cross-region **inference profile** id
> (`us.anthropic.…`), not the bare `anthropic.…` id.

### Build the knowledge base (one time)
```bash
python -m xbb backfill      # pull bookmarks   (--resume to continue past a rate limit)
python -m xbb index         # embed them       (resumable)
python -m xbb categorize    # derive taxonomy + label everything   (resumable)
```
All three only process what's new, so they're safe to re-run.

### Run the app
```bash
./run.sh                    # serves http://127.0.0.1:8000  (Ctrl-C to stop)
```
Data persists on disk in `data/xbb.db`, so you can stop/start freely. Inside the app, the
**Sync** button re-runs backfill → index → label for new bookmarks without the CLI.

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

- **X ingestion** (`xbb.ingestion`) — wraps X's internal GraphQL Bookmarks endpoint behind
  one client; tests feed recorded payloads, never the live network.
- **AI / Bedrock** (`xbb.ai`) — wraps embeddings, labeling, and answers behind one interface;
  tests substitute a deterministic fake.

Storage is a single SQLite file (`xbb.storage`, WAL mode): posts, authors, a discovered
taxonomy, multi-label assignments, and embedding vectors. Search loads the vectors into numpy
and ranks by cosine locally — no vector service, nothing leaving the machine.

## Commands

| Command | Purpose |
|---|---|
| `python -m xbb backfill [--resume]` | pull bookmarks (resume past a rate limit) |
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

Single-user, local-first. Your bookmarks and index live only in `data/xbb.db` on your
machine. The only outbound data is a search query or a question (to Bedrock), plus the
handful of posts retrieved to ground an answer — never your library in bulk.

## More

See [`docs/PRD.md`](docs/PRD.md) for the full product spec — problem, solution, user stories,
implementation & testing decisions, and what's explicitly out of scope (browser-extension
live capture, third-party reply capture, media/vision analysis, a knowledge graph, and
multi-user/hosted deployment).

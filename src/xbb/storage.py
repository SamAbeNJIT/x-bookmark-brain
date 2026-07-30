"""Persistence on Postgres (Neon) for bookmarks, authors, taxonomy, assignments, vectors.

Multi-tenant from the schema down: every tenant-owned table carries a ``tenant_id`` that
defaults from the ``app.current_tenant`` session GUC, and Row-Level Security policies scope
reads/writes to it. The app sets the GUC once per connection (see ``connect``).

Isolation status: the policies are defined and FORCEd now, so the schema is "tenant-ready".
DB-enforced isolation becomes airtight once the app connects as a restricted (non-owner)
role — that lands with multi-tenant auth (plan Inc 3). Until then there is a single tenant
(you), so there is no other tenant's data to leak regardless. Writes are already stamped
with the right ``tenant_id`` via the column DEFAULT.

Vectors live in the ``embeddings`` table as pgvector ``vector(1024)`` with an HNSW cosine
index; semantic search is a single ``ORDER BY vector <=> query`` in ``search.py``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg
from pgvector.psycopg import register_vector

from .config import DEFAULT_TENANT_ID


TEST_DATABASE_NAME = "xbookmarkbrain_test"


def replace_database_name(dsn: str, database: str = TEST_DATABASE_NAME) -> str:
    """Replace only a URL DSN's database path, preserving credentials and query options."""
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("DATABASE_URL must be a postgres:// or postgresql:// URL")
    if not database or "/" in database:
        raise ValueError("database name must be a non-empty path segment")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{quote(database, safe='')}",
                       parsed.query, parsed.fragment))


def database_identity(dsn: str) -> tuple[str, str, int | None, str]:
    """Normalized server/database identity, intentionally ignoring credentials and options."""
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("DATABASE_URL must be a postgres:// or postgresql:// URL")
    return (parsed.scheme.replace("postgresql", "postgres"), parsed.hostname.lower(),
            parsed.port or 5432, unquote(parsed.path.lstrip("/")))


def assert_distinct_database_urls(development_dsn: str, test_dsn: str) -> None:
    """Fail closed before schema initialization or destructive test truncation."""
    if database_identity(development_dsn) == database_identity(test_dsn):
        raise RuntimeError("Refusing destructive test operation: development and test DSNs match")


def _tenant(tenant_id: str | None) -> str:
    """Resolve the active tenant: explicit arg, else env, else the single-user default."""
    return tenant_id or os.getenv("XBB_TENANT_ID", DEFAULT_TENANT_ID)

# Tables owned by a tenant (get tenant_id + RLS). Order matters for FK creation.
_TENANT_TABLES = ("authors", "posts", "self_thread_posts", "categories", "assignments",
                  "embeddings", "sync_state", "usage_events")

# current_setting(..., true) = missing_ok: returns NULL if the GUC is unset, so an
# unscoped connection writes NULL into a NOT NULL column (fails closed) and matches no rows.
_TENANT_DEFAULT = "current_setting('app.current_tenant', true)::uuid"

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS accounts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- this id IS the tenant_id
    email      text UNIQUE,               -- nullable: X-sign-in accounts may have no email yet
    x_user_id  text,                      -- X account id (sign-in-with-X identity; unique via index)
    x_handle   text,                      -- @username for alerts/UI display
    created_at timestamptz NOT NULL DEFAULT now(),
    plan       text NOT NULL DEFAULT 'free',
    subscription_status   text,        -- 'active' | 'past_due' | 'canceled' | NULL
    stripe_customer_id    text,
    stripe_subscription_id text,
    monthly_quota_usd     double precision, -- per-account cap; NULL = use the config default
    credit_balance_usd    double precision NOT NULL DEFAULT 0,   -- prepaid credits, drawn down by asks
    ingestion_paid        boolean NOT NULL DEFAULT false,        -- legacy/comped: TRUE = unlimited import
    import_limit          integer NOT NULL DEFAULT 0,            -- purchased entitlement beyond the free slice
    import_payment_intent text,              -- latest import purchase (for the unused-refund true-up)
    import_paid_usd       double precision   -- $ paid on that purchase
);

CREATE TABLE IF NOT EXISTS authors (
    tenant_id     uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    id            text NOT NULL,
    handle        text,
    display_name  text,
    avatar_url    text,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS posts (
    tenant_id      uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    id             text NOT NULL,        -- X post id, with tenant_id the upsert target
    source         text NOT NULL DEFAULT 'x',
    url            text,
    text           text,
    lang           text,
    created_at     text,
    bookmarked_at  text,
    author_id      text,
    kind           text,                 -- 'original' | 'reply' | 'quote'
    parent_post_id text,
    media_json     text,                 -- [{{url, alt_text, type}}]
    hashtags_json  text,
    links_json     text,
    like_count     integer,
    repost_count   integer,
    raw_json       text,                 -- original X payload, retained verbatim
    bm_rank        bigint,               -- bookmark-recency rank; higher = more recently saved
    label_attempted integer,             -- 1 once labeling has been tried
    text_tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, author_id) REFERENCES authors (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS self_thread_posts (
    tenant_id    uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    root_post_id text NOT NULL,
    position     integer NOT NULL,
    post_id      text NOT NULL,
    PRIMARY KEY (tenant_id, root_post_id, position)
);

CREATE TABLE IF NOT EXISTS categories (
    tenant_id  uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,  -- globally unique surrogate
    name       text,
    definition text,
    parent     text,
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS assignments (
    tenant_id   uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    post_id     text NOT NULL,
    category_id bigint NOT NULL REFERENCES categories (id),
    confidence  double precision,                   -- labeler's fit score (NULL = pre-confidence rows)
    PRIMARY KEY (tenant_id, post_id, category_id)   -- multi-label, one row per (post, category)
);

CREATE TABLE IF NOT EXISTS embeddings (
    tenant_id uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    post_id   text NOT NULL,
    vector    vector(1024),
    PRIMARY KEY (tenant_id, post_id)
);

CREATE INDEX IF NOT EXISTS embeddings_vector_hnsw
    ON embeddings USING hnsw (vector vector_cosine_ops);

CREATE TABLE IF NOT EXISTS sync_state (
    tenant_id uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    key       text NOT NULL,   -- e.g. 'bookmarks_cursor', 'x_oauth'
    value     text,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS usage_events (
    tenant_id     uuid NOT NULL DEFAULT {_TENANT_DEFAULT},
    id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    model         text,
    input_tokens  integer,
    output_tokens integer,
    cost_usd      double precision     -- metered at the AI seam (see usage.cost_of)
);
"""


def _apply_rls(con: psycopg.Connection) -> None:
    """Enable + FORCE RLS and (re)create the tenant-isolation policy on every tenant table."""
    for table in _TENANT_TABLES:
        con.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        con.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        con.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        con.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {_TENANT_DEFAULT}) "
            f"WITH CHECK (tenant_id = {_TENANT_DEFAULT})"
        )


def _execscript(con: psycopg.Connection, sql: str) -> None:
    """Run a multi-statement DDL script (psycopg sends one command per execute)."""
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for stmt in (s.strip() for s in no_comments.split(";")):
        if stmt:
            con.execute(stmt)


# The restricted role the web app connects as so RLS is actually enforced (it lacks BYPASSRLS,
# unlike the Neon owner). Created out-of-band by scripts/setup_app_role.py; granted here if present.
APP_ROLE = "xbb_app"


def _apply_grants(con: psycopg.Connection) -> None:
    """Grant connect/table/sequence privileges to the restricted app role, if it exists."""
    db = con.execute("SELECT current_database()").fetchone()[0]
    con.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT CONNECT ON DATABASE "{db}" TO {APP_ROLE};
            GRANT USAGE ON SCHEMA public TO {APP_ROLE};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
          END IF;
        END $$;
        """
    )


# Idempotent column adds for tables created before a feature shipped (Postgres ADD COLUMN IF NOT
# EXISTS). New tables get the columns from SCHEMA; existing ones get them here.
_MIGRATIONS = (
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS plan text NOT NULL DEFAULT 'free'",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS subscription_status text",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS stripe_customer_id text",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS stripe_subscription_id text",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS monthly_quota_usd double precision",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS credit_balance_usd double precision NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS ingestion_paid boolean NOT NULL DEFAULT false",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS import_limit integer NOT NULL DEFAULT 0",
    # Hybrid search: lexical leg. Column must exist before the index (order matters here).
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS text_tsv tsvector "
    "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED",
    "CREATE INDEX IF NOT EXISTS posts_text_tsv_gin ON posts USING gin (text_tsv)",
    # Sign in with X: X identity on accounts; email becomes optional (X-only accounts).
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS x_user_id text",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS x_handle text",
    "ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS accounts_x_user_id_key ON accounts (x_user_id)",
    # Refund-the-unused import true-up: remember the latest import payment.
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS import_payment_intent text",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS import_paid_usd double precision",
    # Confidence-gated labeling: store the labeler's fit score per assignment.
    "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS confidence double precision",
    # Multi-source (browser bookmark import, PR #19): tables created pre-source need the
    # column — the PR added it to SCHEMA only, which never reaches an existing database.
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'x'",
)


def init_db(dsn: str, tenant_id: str | None = None) -> None:
    """Create the extension, tables, vector index, RLS policies, grants, default account.

    Safe to run repeatedly. Run as the database owner (DDL); the app connects as APP_ROLE.
    """
    tid = _tenant(tenant_id)
    with psycopg.connect(dsn) as con:
        con.execute("SELECT set_config('app.current_tenant', %s, false)", (tid,))
        _execscript(con, SCHEMA)
        for stmt in _MIGRATIONS:
            con.execute(stmt)
        _apply_rls(con)
        _apply_grants(con)
        # Seed the default/single-user (owner) account: pre-paid + funded so local use is never
        # gated by billing. Real signups create their own accounts starting unpaid with $0.
        # (email is a valid-format placeholder — Stripe rejects no-TLD addresses.)
        con.execute(
            "INSERT INTO accounts (id, email, ingestion_paid, credit_balance_usd) "
            "VALUES (%s, %s, true, 1000000) ON CONFLICT (id) DO NOTHING",
            (tid, "local@bookmarkbrain.app"),
        )
        con.commit()


def connect(dsn: str, tenant_id: str | None = None) -> psycopg.Connection:
    """Open a tenant-scoped connection: bind app.current_tenant and register the vector type."""
    con = psycopg.connect(dsn)
    con.execute("SELECT set_config('app.current_tenant', %s, false)", (_tenant(tenant_id),))
    con.commit()
    register_vector(con)
    return con


# --------------------------------------------------------------------------- key/value state
# RLS scopes these to the current tenant; inserts get tenant_id from the column DEFAULT.


def get_state(con: psycopg.Connection, key: str) -> str | None:
    """Read an arbitrary value from the sync_state store (e.g. OAuth tokens JSON)."""
    row = con.execute("SELECT value FROM sync_state WHERE key = %s", (key,)).fetchone()
    return row[0] if row and row[0] else None


def set_state(con: psycopg.Connection, key: str, value: str | None) -> None:
    """Write/clear an arbitrary sync_state value."""
    if value is None:
        con.execute("DELETE FROM sync_state WHERE key = %s", (key,))
    else:
        con.execute(
            "INSERT INTO sync_state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id, key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    con.commit()


def claim_state(con: psycopg.Connection, key: str, value: str) -> bool:
    """Atomically create tenant state once. The unique tenant/key pair is the claim boundary."""
    row = con.execute(
        "INSERT INTO sync_state (key, value) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id, key) DO NOTHING RETURNING key",
        (key, value),
    ).fetchone()
    con.commit()
    return row is not None


def set_pkce(con: psycopg.Connection, state: str, verifier: str) -> None:
    """Stash an OAuth PKCE verifier (keyed by state) in the DB so the login→callback handshake
    survives across web instances (App Runner autoscaling)."""
    set_state(con, f"pkce:{state}", verifier)


def pop_pkce(con: psycopg.Connection, state: str) -> str | None:
    """Fetch and delete the PKCE verifier for a state (one-time use)."""
    key = f"pkce:{state}"
    verifier = get_state(con, key)
    if verifier is not None:
        set_state(con, key, None)
    return verifier


def get_or_create_account(con: psycopg.Connection, email: str) -> str:
    """Return the account id for an email, creating the account on first sign-in. (No RLS on
    accounts — it's the tenant registry, queried by email before a tenant is known.)"""
    row = con.execute("SELECT id FROM accounts WHERE email = %s", (email,)).fetchone()
    if row:
        return str(row[0])
    row = con.execute(
        "INSERT INTO accounts (email) VALUES (%s) RETURNING id", (email,)
    ).fetchone()
    con.commit()
    return str(row[0])


def get_account_email(con: psycopg.Connection, account_id: str) -> str | None:
    row = con.execute("SELECT email FROM accounts WHERE id = %s", (account_id,)).fetchone()
    return row[0] if row else None


def set_account_email(con: psycopg.Connection, account_id: str, email: str) -> bool:
    """Attach an email to an email-less account (captured from Stripe checkout). Guarded so it
    never overwrites an existing email; if another account already owns the address (unique),
    skip quietly — capture is opportunistic, never a failure path. Returns True if saved."""
    try:
        cur = con.execute(
            "UPDATE accounts SET email = %s WHERE id = %s AND email IS NULL",
            (email, account_id))
        con.commit()
        return cur.rowcount > 0
    except psycopg.errors.UniqueViolation:
        con.rollback()
        return False


def set_import_payment(con: psycopg.Connection, account_id: str,
                       payment_intent: str | None, paid_usd: float | None) -> None:
    """Remember (or clear, with Nones) the latest import purchase for the refund true-up.
    Latest overwrites previous — one outstanding true-up at a time."""
    con.execute("UPDATE accounts SET import_payment_intent = %s, import_paid_usd = %s WHERE id = %s",
                (payment_intent, paid_usd, account_id))
    con.commit()


def get_import_payment(con: psycopg.Connection) -> tuple[str | None, float]:
    """The current tenant's outstanding import purchase (RLS-safe read via own row lookup)."""
    row = con.execute(
        "SELECT import_payment_intent, import_paid_usd FROM accounts "
        "WHERE id = current_setting('app.current_tenant', true)::uuid").fetchone()
    return (row[0], float(row[1] or 0)) if row else (None, 0.0)


def account_by_x_user_id(con: psycopg.Connection, x_user_id: str) -> str | None:
    """Account id for an X identity (sign-in-with-X lookup, pre-tenant → no RLS on accounts)."""
    row = con.execute("SELECT id FROM accounts WHERE x_user_id = %s", (x_user_id,)).fetchone()
    return str(row[0]) if row else None


def create_account_from_x(con: psycopg.Connection, x_user_id: str, x_handle: str | None) -> str:
    """First X sign-in: create an email-less account keyed by the X identity."""
    row = con.execute(
        "INSERT INTO accounts (x_user_id, x_handle) VALUES (%s, %s) RETURNING id",
        (x_user_id, x_handle),
    ).fetchone()
    con.commit()
    return str(row[0])


def set_account_x_identity(con: psycopg.Connection, account_id: str,
                           x_user_id: str, x_handle: str | None) -> None:
    """Link an X identity to an existing (e.g. email) account, so a later 'Sign in with X'
    lands in the same account. Best-effort: if another account already claims the identity
    (unique index), keep the existing claim rather than fail the caller's flow."""
    try:
        con.execute("UPDATE accounts SET x_user_id = %s, x_handle = %s WHERE id = %s",
                    (x_user_id, x_handle, account_id))
        con.commit()
    except psycopg.errors.UniqueViolation:
        con.rollback()


# --------------------------------------------------------------------------- billing / plan
# accounts has no RLS (it's the tenant registry); the webhook updates rows across tenants.


def set_subscription(con: psycopg.Connection, account_id: str, *, plan: str,
                     subscription_status: str | None, stripe_customer_id: str | None = None,
                     stripe_subscription_id: str | None = None,
                     monthly_quota_usd: float | None = None) -> None:
    """Update an account's plan/subscription state (COALESCE keeps existing Stripe ids if None)."""
    con.execute(
        "UPDATE accounts SET plan = %s, subscription_status = %s, "
        "stripe_customer_id = COALESCE(%s, stripe_customer_id), "
        "stripe_subscription_id = COALESCE(%s, stripe_subscription_id), "
        "monthly_quota_usd = %s WHERE id = %s",
        (plan, subscription_status, stripe_customer_id, stripe_subscription_id,
         monthly_quota_usd, account_id),
    )
    con.commit()


def account_by_stripe_customer(con: psycopg.Connection, customer_id: str) -> str | None:
    row = con.execute(
        "SELECT id FROM accounts WHERE stripe_customer_id = %s", (customer_id,)
    ).fetchone()
    return str(row[0]) if row else None


def get_account_billing(con: psycopg.Connection, account_id: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT plan, subscription_status, monthly_quota_usd FROM accounts WHERE id = %s",
        (account_id,),
    ).fetchone()
    if not row:
        return {"plan": "free", "subscription_status": None, "monthly_quota_usd": None}
    return {"plan": row[0], "subscription_status": row[1], "monthly_quota_usd": row[2]}


def account_monthly_quota(con: psycopg.Connection) -> float | None:
    """The current tenant's per-account spend cap (USD), or None if unset (use config default)."""
    row = con.execute(
        "SELECT monthly_quota_usd FROM accounts "
        "WHERE id = current_setting('app.current_tenant', true)::uuid"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


# --------------------------------------------------------------------------- credits
# Prepaid balance drawn down by billable actions (asks). All scoped to the current tenant.


def credit_balance(con: psycopg.Connection) -> float:
    row = con.execute(
        "SELECT credit_balance_usd FROM accounts "
        "WHERE id = current_setting('app.current_tenant', true)::uuid"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def add_credits(con: psycopg.Connection, account_id: str, amount_usd: float) -> None:
    """Top up an account's prepaid balance (called from the Stripe one-time-payment webhook)."""
    con.execute(
        "UPDATE accounts SET credit_balance_usd = credit_balance_usd + %s WHERE id = %s",
        (amount_usd, account_id),
    )
    con.commit()


def debit_credits(con: psycopg.Connection, amount_usd: float) -> bool:
    """Atomically charge the current tenant if it has the balance. Returns False if insufficient
    (the WHERE clause prevents a balance from ever going negative under concurrent asks)."""
    row = con.execute(
        "UPDATE accounts SET credit_balance_usd = credit_balance_usd - %s "
        "WHERE id = current_setting('app.current_tenant', true)::uuid "
        "AND credit_balance_usd >= %s RETURNING credit_balance_usd",
        (amount_usd, amount_usd),
    ).fetchone()
    con.commit()
    return row is not None


def use_free_ask(con: psycopg.Connection, daily_limit: int) -> bool:
    """Consume one of today's free asks if any remain. Returns True if a free ask was granted.

    The counter lives in sync_state under a per-day key (RLS scopes it to the tenant); the
    conditional UPDATE makes the increment atomic, so concurrent asks can't exceed the limit.
    """
    key_row = con.execute("SELECT to_char(now(), 'YYYY-MM-DD')").fetchone()
    key = f"free_asks:{key_row[0]}"
    con.execute(
        "INSERT INTO sync_state (key, value) VALUES (%s, '0') ON CONFLICT (tenant_id, key) DO NOTHING",
        (key,),
    )
    row = con.execute(
        "UPDATE sync_state SET value = (value::int + 1)::text "
        "WHERE key = %s AND value::int < %s RETURNING value",
        (key, daily_limit),
    ).fetchone()
    con.commit()
    return row is not None


def free_asks_used_today(con: psycopg.Connection) -> int:
    row = con.execute(
        "SELECT value::int FROM sync_state WHERE key = 'free_asks:' || to_char(now(), 'YYYY-MM-DD')"
    ).fetchone()
    return int(row[0]) if row else 0


def refund_credits(con: psycopg.Connection, amount_usd: float) -> None:
    """Return a debited amount to the current tenant (e.g. when an ask fails after charging)."""
    con.execute(
        "UPDATE accounts SET credit_balance_usd = credit_balance_usd + %s "
        "WHERE id = current_setting('app.current_tenant', true)::uuid",
        (amount_usd,),
    )
    con.commit()


def import_limit(con: psycopg.Connection) -> int:
    """The current tenant's PURCHASED import entitlement (bookmarks beyond the free slice)."""
    row = con.execute(
        "SELECT import_limit FROM accounts "
        "WHERE id = current_setting('app.current_tenant', true)::uuid"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def add_import_limit(con: psycopg.Connection, account_id: str, n: int) -> None:
    """Raise an account's purchased import entitlement by n bookmarks (from a paid checkout)."""
    con.execute(
        "UPDATE accounts SET import_limit = import_limit + %s WHERE id = %s", (n, account_id)
    )
    con.commit()


def reduce_import_limit(con: psycopg.Connection, n: int) -> None:
    """Shrink the current tenant's purchased entitlement (unused capacity → credits conversion)."""
    con.execute(
        "UPDATE accounts SET import_limit = GREATEST(import_limit - %s, 0) "
        "WHERE id = current_setting('app.current_tenant', true)::uuid",
        (n,),
    )
    con.commit()


def effective_import_cap(con: psycopg.Connection, free_limit: int) -> int | None:
    """Total X posts this tenant may store: None = unlimited (comped/legacy ingestion_paid),
    else the free X slice + purchased imports. Non-X sources are unlimited/free and never
    consume or reduce this X-only entitlement."""
    if is_ingestion_paid(con):
        return None
    return free_limit + import_limit(con)


def imports_available(con: psycopg.Connection, free_limit: int) -> int | None:
    """Unused purchased X imports after this tenant's stored X overage.

    None means unlimited/comped. Non-X posts are unlimited/free and never touch the pool;
    the balance is derived from X counts rather than decremented, so the math cannot drift.
    """
    if is_ingestion_paid(con):
        return None
    x_over = max(0, post_count(con, "x") - free_limit)
    return max(0, import_limit(con) - x_over)


def is_ingestion_paid(con: psycopg.Connection) -> bool:
    row = con.execute(
        "SELECT ingestion_paid FROM accounts "
        "WHERE id = current_setting('app.current_tenant', true)::uuid"
    ).fetchone()
    return bool(row and row[0])


def post_count(con: psycopg.Connection, source: str | None = None) -> int:
    """This tenant's stored posts, optionally scoped to one source.

    Entitlement math must scope to ``x``; every non-X source is unlimited/free and must
    never consume or inflate the paid X slice.
    """
    if source is None:
        return con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM posts WHERE source = %s", (source,)
    ).fetchone()[0]


def is_capped_free(con: psycopg.Connection, free_limit: int) -> bool:
    """Is this tenant a free account sitting at the free-import cap? The single predicate
    behind every "complete your library" upsell surface — any purchase flips it false
    everywhere at once. NOTE: a user whose entire timeline is exactly <= free_limit still
    counts (X has no bookmark-count API, so "more pages exist" is unknowable without a paid
    fetch); sub-limit users are provably uncapped and see nothing."""
    if is_ingestion_paid(con) or import_limit(con) > 0:
        return False
    return post_count(con, "x") >= free_limit


def library_more_exists(con: psycopg.Connection) -> bool:
    """Has a sync PROVEN this tenant's X library holds more than what's stored? Set by
    backfill when it fetched a bookmark it couldn't store (cap full). Absent = ambiguous
    (the cap may equal their entire library) — upsell copy must hedge accordingly."""
    row = con.execute(
        "SELECT value FROM sync_state WHERE key = 'library_more_exists'"
    ).fetchone()
    return bool(row and row[0] == "1")


def increment_total_asks(con: psycopg.Connection) -> int:
    """Bump the tenant's lifetime answered-question counter; returns the new total.
    Powers the "first successful answer" upsell trigger (existing tenants start at 0, so
    their next answer counts as #1 — deliberate: the current cohort gets one impression)."""
    con.execute(
        "INSERT INTO sync_state (key, value) VALUES ('asks_total', '0') "
        "ON CONFLICT (tenant_id, key) DO NOTHING"
    )
    row = con.execute(
        "UPDATE sync_state SET value = (value::int + 1)::text "
        "WHERE key = 'asks_total' RETURNING value"
    ).fetchone()
    con.commit()
    return int(row[0])


def set_ingestion_paid(con: psycopg.Connection, account_id: str, paid: bool = True) -> None:
    con.execute("UPDATE accounts SET ingestion_paid = %s WHERE id = %s", (paid, account_id))
    con.commit()


# --------------------------------------------------------------------------- usage metering


def record_usage(con: psycopg.Connection, model: str, input_tokens: int,
                 output_tokens: int, cost_usd: float) -> None:
    """Record one metered AI call for the current tenant (RLS stamps tenant_id)."""
    con.execute(
        "INSERT INTO usage_events (model, input_tokens, output_tokens, cost_usd) "
        "VALUES (%s, %s, %s, %s)",
        (model, input_tokens, output_tokens, cost_usd),
    )
    con.commit()


def usage_this_month(con: psycopg.Connection) -> float:
    """Total metered spend (USD) for the current tenant since the start of this month."""
    row = con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events "
        "WHERE ts >= date_trunc('month', now())"
    ).fetchone()
    return float(row[0]) if row else 0.0


def get_sync_cursor(con: psycopg.Connection) -> str | None:
    """The saved bookmarks pagination cursor to resume from, or None (never started / done)."""
    return get_state(con, "bookmarks_cursor")


def set_sync_cursor(con: psycopg.Connection, cursor: str | None) -> None:
    """Persist where the next backfill should resume. None clears it (the sync finished)."""
    set_state(con, "bookmarks_cursor", cursor)

"""X ToS compliance: delete a tweet (by id) from ALL tenants within the 24h window.

The X Developer Agreement requires deleting stored X Content that is deleted/protected/withheld
on X, within 24 hours of a written request. Run with the tweet id(s):

    .venv/bin/python scripts/compliance_delete.py 1234567890 [more_ids...]
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from xbb import storage
from xbb.config import Config


def main() -> int:
    load_dotenv()
    ids = sys.argv[1:]
    if not ids:
        print(__doc__)
        return 2
    cfg = Config.from_env()
    con = storage.connect(cfg.database_url, cfg.tenant_id)  # owner: crosses all tenants
    try:
        for tid in ids:
            a = con.execute("DELETE FROM assignments WHERE post_id = %s", (tid,)).rowcount
            e = con.execute("DELETE FROM embeddings WHERE post_id = %s", (tid,)).rowcount
            p = con.execute("DELETE FROM posts WHERE id = %s", (tid,)).rowcount
            con.commit()
            print(f"{tid}: posts={p} embeddings={e} assignments={a} — purged across all tenants")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

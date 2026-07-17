"""On-demand, cost-bounded data builder for the personal knowledge graph.

The graph performs one ``bm_rank``-capped post scan and one buffered, index-backed ANN
batch only when the page is opened. With the defaults, at most 400 HNSW probes return
40 candidates each (at most 16,000 candidate rows before Python filtering). Work scales
with ``node_cap``, not corpus size; there is no full-corpus vector load or persistent worker.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import psycopg

from .templates import parent_color

DEFAULT_NODE_CAP = 400
DEFAULT_KNN_K = 6
DEFAULT_SIM_THRESHOLD = 0.5
DEFAULT_MAX_EDGES = 4000
_ANN_BUFFER = 40


def _parent_color(parent: str | None) -> str | None:
    """Keep graph colors in lockstep with the server-rendered UI palette."""
    return parent_color(parent)


def _post_nodes(con: psycopg.Connection, node_cap: int) -> tuple[list[dict[str, Any]], set[str]]:
    rows = con.execute(
        """
        SELECT p.id, p.url, p.text, c.parent, p.bm_rank
        FROM posts p
        JOIN embeddings e ON e.tenant_id = p.tenant_id AND e.post_id = p.id
        LEFT JOIN LATERAL (
            SELECT category_id AS cid
            FROM assignments
            WHERE tenant_id = p.tenant_id AND post_id = p.id
            ORDER BY category_id
            LIMIT 1
        ) pa ON true
        LEFT JOIN categories c ON c.tenant_id = p.tenant_id AND c.id = pa.cid
        ORDER BY p.bm_rank DESC NULLS LAST, p.id
        LIMIT %s
        """,
        (node_cap,),
    ).fetchall()
    nodes = []
    for post_id, url, text, parent, rank in rows:
        theme = parent or "Other"
        nodes.append({
            "id": f"post:{post_id}", "type": "post", "label": (text or "")[:120],
            "url": url, "parent": theme, "color": _parent_color(theme), "rank": rank,
        })
    return nodes, {str(r[0]) for r in rows}


def _category_nodes(con: psycopg.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT c.id, c.name, c.parent, COUNT(a.post_id)
        FROM categories c
        LEFT JOIN assignments a
          ON a.tenant_id = c.tenant_id AND a.category_id = c.id
        GROUP BY c.id, c.name, c.parent
        ORDER BY c.id
        """
    ).fetchall()
    return [
        {"id": f"cat:{cid}", "type": "category", "label": name,
         "parent": parent or "Other", "color": _parent_color(parent or "Other"),
         "count": count}
        for cid, name, parent, count in rows if count
    ]


def _theme_id(parent: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", parent.lower()).strip("-")
    return f"theme:{slug or 'other'}"


def _theme_nodes_and_edges(
    post_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counts = Counter(node.get("parent") or "Other" for node in post_nodes)
    themes = [
        {"id": _theme_id(parent), "type": "theme", "label": parent,
         "color": _parent_color(parent), "count": count}
        for parent, count in sorted(counts.items())
    ]
    edges = [
        {"source": node["id"], "target": _theme_id(node.get("parent") or "Other"),
         "kind": "theme"}
        for node in post_nodes
    ]
    return themes, edges


def _user_root(
    total_posts: int, theme_nodes: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = {"id": "user:me", "type": "user", "label": "You", "count": total_posts}
    largest = max((int(node["count"]) for node in theme_nodes), default=0)
    spokes = [
        {"source": "user:me", "target": node["id"], "kind": "ownership",
         "weight": round(int(node["count"]) / largest, 4) if largest else 0.0}
        for node in theme_nodes
    ]
    return root, spokes


def _membership_edges(con: psycopg.Connection, post_ids: set[str]) -> list[dict[str, Any]]:
    if not post_ids:
        return []
    rows = con.execute(
        "SELECT post_id, category_id FROM assignments WHERE post_id = ANY(%s) "
        "ORDER BY post_id, category_id",
        (list(post_ids),),
    ).fetchall()
    return [
        {"source": f"post:{post_id}", "target": f"cat:{category_id}",
         "kind": "membership"}
        for post_id, category_id in rows
    ]


def _similarity_edges(
    con: psycopg.Connection,
    post_ids: set[str],
    knn_k: int,
    sim_threshold: float,
    max_edges: int,
) -> list[dict[str, Any]]:
    if len(post_ids) < 2 or knn_k <= 0 or max_edges <= 0:
        return []
    # SET LOCAL applies to the current request transaction and raises ANN recall enough for
    # the buffered candidate set without changing the connection globally.
    con.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(_ANN_BUFFER * 4),))
    rows = con.execute(
        """
        SELECT src.post_id AS a, nbr.post_id AS b, (src.vector <=> nbr.vector) AS dist
        FROM embeddings src
        JOIN LATERAL (
            SELECT e.post_id, e.vector
            FROM embeddings e
            WHERE e.tenant_id = src.tenant_id
              AND e.post_id <> src.post_id
            ORDER BY e.vector <=> src.vector
            LIMIT %s
        ) nbr ON true
        WHERE src.post_id = ANY(%s)
          AND src.tenant_id = current_setting('app.current_tenant')::uuid
        ORDER BY src.post_id, dist, nbr.post_id
        """,
        (_ANN_BUFFER, list(post_ids)),
    ).fetchall()

    allowed = set(post_ids)
    per_source: dict[str, list[tuple[str, float]]] = {}
    for a, b, distance in rows:
        a, b = str(a), str(b)
        similarity = 1.0 - float(distance)
        if b in allowed and similarity >= sim_threshold:
            per_source.setdefault(a, []).append((b, similarity))

    pairs: dict[tuple[str, str], float] = {}
    for source, candidates in per_source.items():
        candidates.sort(key=lambda item: (-item[1], item[0]))
        for target, similarity in candidates[:knn_k]:
            pair = tuple(sorted((source, target)))
            pairs[pair] = max(pairs.get(pair, 0.0), similarity)

    ranked = sorted(pairs.items(), key=lambda item: (-item[1], item[0]))[:max_edges]
    return [
        {"source": f"post:{a}", "target": f"post:{b}", "kind": "similarity",
         "weight": round(weight, 4)}
        for (a, b), weight in ranked
    ]


def build_graph(
    con: psycopg.Connection,
    *,
    node_cap: int = DEFAULT_NODE_CAP,
    knn_k: int = DEFAULT_KNN_K,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    max_edges: int = DEFAULT_MAX_EDGES,
    include_posts: bool = True,
    include_categories: bool = True,
) -> dict[str, Any]:
    """Build a visualization-agnostic graph for the connection's bound tenant."""
    node_cap = max(0, int(node_cap))
    knn_k = max(0, int(knn_k))
    max_edges = max(0, int(max_edges))
    sim_threshold = max(-1.0, min(1.0, float(sim_threshold)))
    total_posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    embedded_posts = con.execute(
        "SELECT COUNT(*) FROM posts p JOIN embeddings e "
        "ON e.tenant_id = p.tenant_id AND e.post_id = p.id"
    ).fetchone()[0]

    post_nodes, post_ids = _post_nodes(con, node_cap) if include_posts else ([], set())
    category_nodes = _category_nodes(con) if include_categories else []
    theme_nodes, theme_edges = _theme_nodes_and_edges(post_nodes)
    root, ownership_edges = _user_root(total_posts, theme_nodes)

    membership_edges = _membership_edges(con, post_ids) if include_categories and post_ids else []
    category_ids = {node["id"] for node in category_nodes}
    membership_edges = [e for e in membership_edges if e["target"] in category_ids]
    similarity_edges = _similarity_edges(
        con, post_ids, knn_k, sim_threshold, max_edges
    ) if post_ids else []

    nodes = [root] + theme_nodes + post_nodes + category_nodes
    edges = ownership_edges + theme_edges + similarity_edges + membership_edges
    meta = {
        "user_nodes": 1, "theme_nodes": len(theme_nodes), "post_nodes": len(post_nodes),
        "category_nodes": len(category_nodes), "ownership_edges": len(ownership_edges),
        "theme_edges": len(theme_edges), "similarity_edges": len(similarity_edges),
        "membership_edges": len(membership_edges),
        "capped": bool(include_posts and embedded_posts > node_cap),
        "node_cap": node_cap, "knn_k": knn_k, "sim_threshold": sim_threshold,
        "max_edges": max_edges,
    }
    return {"nodes": nodes, "edges": edges, "meta": meta}

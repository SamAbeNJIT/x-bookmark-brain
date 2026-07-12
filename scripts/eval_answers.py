"""Answer-quality A/B: Claude Sonnet (bedrock) vs Grok 4.3 (mantle) on the eval questions.

Same reranked retrieval for both; each model answers; Sonnet judges BLIND (answers are
labeled A/B in random-fixed order per question) on groundedness + usefulness, 1-5 each.
Also reports wall-clock and per-answer cost. Read-only against the owner corpus.

Usage: python scripts/eval_answers.py [n_questions]
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xbb import storage  # noqa: E402
from xbb.ai import BedrockAIClient, MantleAIClient, _extract_json  # noqa: E402
from xbb.rerank import rerank  # noqa: E402
from xbb.search import search  # noqa: E402
from xbb.usage import cost_of  # noqa: E402

from eval_retrieval import OWNER_TENANT, QUESTIONS  # noqa: E402


def _mk(cls, **extra):
    return cls(os.environ["AWS_REGION"],
               embedding_model=os.environ["BEDROCK_EMBEDDING_MODEL"],
               labeling_model=os.environ["BEDROCK_LABELING_MODEL"],
               reasoning_model=os.environ["BEDROCK_REASONING_MODEL"], **extra)


def _judge(judge_ai, question, posts, ans_a, ans_b):
    system = (
        "You are judging two answers to a question about a user's bookmark library. "
        "Score each answer 1-5 on groundedness (claims supported by the provided posts) "
        "and 1-5 on usefulness (actually answers the question, well organized). Reply "
        "with ONLY JSON: {\"a\": {\"grounded\": n, \"useful\": n}, \"b\": {\"grounded\": n, \"useful\": n}}."
    )
    ctx = json.dumps([{"id": p["id"], "text": (p.get("text") or "")[:400]} for p in posts[:15]])
    user = f"Question: {question}\n\nPosts:\n{ctx}\n\nAnswer A:\n{ans_a}\n\nAnswer B:\n{ans_b}"
    return _extract_json(judge_ai._invoke_claude(judge_ai.reasoning_model, system, user))


def main(n: int) -> None:
    con = storage.connect(os.environ["DATABASE_URL"], OWNER_TENANT)
    sonnet = _mk(BedrockAIClient)
    grok = _mk(MantleAIClient, mantle_api_key=os.environ["BEDROCK_API_KEY"])
    totals = {"sonnet": {"g": 0, "u": 0, "t": 0.0, "c": 0.0},
              "grok": {"g": 0, "u": 0, "t": 0.0, "c": 0.0}}
    for i, q in enumerate(QUESTIONS[:n]):
        posts = rerank(sonnet, q, search(con, sonnet, q, 100), 30)
        sonnet.pop_usage()
        t0 = time.time(); ans_s = sonnet.answer(q, posts); ts = time.time() - t0
        cs = sum(cost_of(e["model"], e["input_tokens"], e["output_tokens"]) for e in sonnet.pop_usage())
        t0 = time.time(); ans_g = grok.answer(q, posts); tg = time.time() - t0
        cg = sum(cost_of(e["model"], e["input_tokens"], e["output_tokens"]) for e in grok.pop_usage())
        # blind labels: even questions -> sonnet is A; odd -> grok is A
        s_is_a = i % 2 == 0
        a, b = (ans_s, ans_g) if s_is_a else (ans_g, ans_s)
        v = _judge(sonnet, q, posts, a["answer"], b["answer"])
        vs, vg = (v["a"], v["b"]) if s_is_a else (v["b"], v["a"])
        totals["sonnet"]["g"] += vs["grounded"]; totals["sonnet"]["u"] += vs["useful"]
        totals["grok"]["g"] += vg["grounded"]; totals["grok"]["u"] += vg["useful"]
        totals["sonnet"]["t"] += ts; totals["sonnet"]["c"] += cs
        totals["grok"]["t"] += tg; totals["grok"]["c"] += cg
        print(f"[{i+1}] {q[:60]}\n  sonnet: g={vs['grounded']} u={vs['useful']} {ts:5.1f}s {cs*100:4.1f}c"
              f"  |  grok: g={vg['grounded']} u={vg['useful']} {tg:5.1f}s {cg*100:4.1f}c")
    con.close()
    n_done = min(n, len(QUESTIONS))
    for name, t in totals.items():
        print(f"{name}: grounded {t['g']/n_done:.1f}/5  useful {t['u']/n_done:.1f}/5  "
              f"avg {t['t']/n_done:.1f}s  avg {t['c']/n_done*100:.1f}c")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)

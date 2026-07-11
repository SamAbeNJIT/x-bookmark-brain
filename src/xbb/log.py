"""App-wide logging: structured-ish lines to stdout, which App Runner ships to CloudWatch.

One place to configure so every module logs consistently:
    from .log import logger
    logger.info("sync.start tenant=%s", tenant_id)

Convention: dot-namespaced event first (sync.start, ask.answered, billing.webhook), then
key=value pairs — grep-friendly in CloudWatch ("sync.error", "tenant=<id>") without needing
a JSON pipeline yet. Never log message content, question text, or tokens — ids and numbers only.
"""

from __future__ import annotations

import logging
import sys

# Deliberately NOT logging.basicConfig(): under uvicorn the root logger is already configured,
# so basicConfig silently no-ops and INFO lines get dropped by the WARNING-level root — which
# made every xbb.* event invisible in CloudWatch for a day. Attach our own handler instead.
logger = logging.getLogger("xbb")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s xbb %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False  # don't double-print through uvicorn's root handler

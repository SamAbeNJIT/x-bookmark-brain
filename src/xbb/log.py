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

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s xbb %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger("xbb")

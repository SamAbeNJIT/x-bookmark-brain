"""x-bookmark-brain: backfill, categorize, and AI-search X bookmarks.

Two architectural seams keep external systems mockable in tests (see docs/PRD.md):
  - ingestion.XClient   — X's internal bookmarks endpoint
  - ai.AIClient         — Amazon Bedrock (labeling, embeddings, answers)
"""

__version__ = "0.0.1"

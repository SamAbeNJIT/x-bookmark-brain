# Domain Docs

How the engineering skills should consume this repo's domain documentation.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the domain glossary / ubiquitous language.
- **`docs/adr/`** — architectural decision records (created lazily as decisions are made).

If any of these don't exist yet, proceed silently.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

When output names a domain concept (issue title, test name, proposal), use the term as
defined in `CONTEXT.md`. Don't drift to synonyms.

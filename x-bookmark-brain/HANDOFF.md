# x-bookmark-brain — handoff / backup

This folder is a **backup stash** of a *separate* new project, parked inside the
`calculator` repo only because the session that created it could not reach a dedicated
GitHub repo (the integration was scoped to `calculator`, so `create_repository` returned
403 and the new project couldn't be pushed elsewhere).

It is **not** part of the calculator project. Extract `x-bookmark-brain/` into its own
repo when ready.

## What this is

`x-bookmark-brain`: a personal, single-user local web app that backfills my entire X
(Twitter) bookmark history, auto-categorizes it (hybrid, multi-label), and lets me find
anything via semantic search + an AI "ask" mode that cites the saved posts. AI runs on
Amazon Bedrock (Claude + Titan embeddings). See `docs/PRD.md` for the full PRD.

The PRD was produced by grilling through the whole design tree and synthesizing it with
Matt Pocock's `to-prd` skill template.

## Tomorrow's checklist (once Claude↔GitHub connection is fixed)

1. Create / connect the dedicated `x-bookmark-brain` GitHub repo.
2. Move this `x-bookmark-brain/` folder into that repo (or copy `docs/PRD.md` over).
3. Install skills **locally** (global `-g` is unsupported for these PromptScript skills):
   `npx skills add mattpocock/skills skill=setup-matt-pocock-skills skill=to-prd skill=to-issues -y`
   (Real skill names are `to-prd` and `to-issues` — there is no `write-a-prd`/`prd-to-plan`.)
4. Run `/setup-matt-pocock-skills` → GitHub issue tracker, default triage labels,
   single-context domain docs.
5. Run `/to-issues` to publish the PRD with the `ready-for-agent` triage label.

# Domain Docs

How engineering skills consume xiaoai-ai's domain documentation. Paths below are relative to the project root containing the agent configuration file.

## Before exploring, read these

- `GLOSSARY.md` at the project root for domain terms and relationships.
- Relevant ADRs under `docs/adr/` for confirmed architectural decisions.

If these files do not exist, proceed silently. Create them lazily through `/domain-modeling` when terms or decisions are resolved.

## File structure

This project uses a single-context layout:

    /
    ├── GLOSSARY.md
    └── docs/adr/
        └── 0001-<decision>.md

## Use the glossary's vocabulary

When naming domain concepts in issues, proposals, hypotheses, or tests, use the terms defined in `GLOSSARY.md`.

If a needed concept is absent, check whether an existing term already covers it; record a genuine vocabulary gap for `/domain-modeling`.

## Flag ADR conflicts

Surface any conflict with an existing ADR explicitly before proposing to reopen the decision. Include the ADR identifier and the reason for reconsidering it.

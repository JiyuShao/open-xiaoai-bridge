# Issue tracker: Local Markdown

Issues and specs for xiaoai-ai live as Markdown files in `.scratch/`, relative to the project root containing the agent configuration file.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- The spec is `.scratch/<feature-slug>/spec.md`.
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file.
- Triage state is recorded as a `Status:` line near the top of each implementation issue file. Use the role strings in [triage-labels.md](triage-labels.md).
- Comments and conversation history append to the bottom of the file under a `## Comments` heading.

## When a skill says "publish to the issue tracker"

Create the corresponding spec or issue file at the path defined above, creating directories as needed.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. If given only an issue number, resolve it within the named feature directory; ask for the feature when the number is ambiguous.

## Wayfinding operations

Used by `/wayfinder`. The map is a file with one child file per ticket. Wayfinding tickets use the lifecycle below rather than the implementation issue triage states.

- Map: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records `research`, `prototype`, `grilling`, or `task`. Set `Status: open` initially, then `claimed` or `resolved`.
- Blocking: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every ticket it lists is `resolved`.
- Frontier: scan the effort's `issues/` directory for open, unblocked tickets; first by number wins.
- Claim: set `Status: claimed` and save before any work.
- Resolve: append the answer under `## Answer`, set `Status: resolved`, then append a context pointer (gist + link) to Decisions-so-far in `map.md`.

# germinate-diff — v1 spec

## Purpose

A tool to show the consequences of a proposed change to an Ubuntu seed
collection: given two refs of a seed git repo, run germinate against each
and diff the resulting expanded per-seed package lists. Intended first as
a local CLI tool, later to be wrapped so it can run automatically and post
its output as a comment on a Launchpad merge proposal.

## Background / constraints

- Germinate takes seed definitions + archive package metadata and produces,
  for each seed, an "expanded" package list (the full closure including
  everything pulled in via dependencies and seed inheritance).
- Some seed collections depend on others — notably the "platform" seed
  collection, which other collections (desktop, server, etc.) build on via
  their `STRUCTURE` file. Only the collection under test should vary
  between the two germinate runs; any other collections it depends on
  should be held fixed at their current/latest checkout for both runs.
- `STRUCTURE` file paths for dependent seed collections are relative, and
  resolved by germinate through logic that isn't well understood yet — see
  Open questions below. Full generic resolution (parsing `STRUCTURE` +
  remotes) is out of scope for v1; a hardcoded map of known collections to
  local paths is sufficient.
- Archive metadata: rather than fetching/pinning archive snapshots, use
  the maintainer's existing per-series `chdist` setup, via germinate's
  `--apt-config` support. Both germinate runs (old-ref and new-ref) use
  the same chdist, so the diff reflects only the seed change, not archive
  drift.
- Germinate has JSON output support (added semi-recently), so output
  should be consumed as structured JSON rather than parsed from its
  ad hoc text format.

## CLI

```
germinate-diff <seed-repo> <old-ref> <new-ref> <chdist-name> [options]
```

- `<seed-repo>`: path to the git repo for the seed collection under test
  (this could be an "outer" collection like desktop/server, or the
  platform collection itself — both are supported, but only one collection
  is under test at a time; simultaneous changes to an outer collection
  *and* platform are out of scope for now).
- `<old-ref>`, `<new-ref>`: git refs (commit hashes or branch names) in
  `<seed-repo>` to diff.
- `<chdist-name>`: the chdist to run germinate against (same chdist used
  for both runs).
- `[options]`: TBD at implementation time (e.g. path to the hardcoded
  collection map, chdist base directory, etc.)

## Mechanism

1. Create two git worktrees (or temp checkouts) of `<seed-repo>`, one at
   `<old-ref>` and one at `<new-ref>`.
2. For any other seed collections the collection under test depends on
   (e.g. platform, when testing an outer collection), use a fixed local
   checkout from a hardcoded map of collection name → local path. This map
   is small and hand-maintained rather than derived from `STRUCTURE` +
   remotes.
3. Run germinate against each of the two worktrees, using `--apt-config`
   pointed at `<chdist-name>`, with JSON output enabled.
4. For each seed, extract the expanded package list from each run's JSON
   output.
5. Diff old vs new per seed: added / removed packages. A seed that exists
   in only one of the two runs (added or removed by the proposed change)
   is not an error — treat its package list as empty on the side where it
   doesn't exist, so the diff shows the seed's entire expanded list as
   all-added or all-removed.
6. Also compute a global diff: the union of expanded packages across all
   seeds, old vs new. This surfaces the net effect on the archive as a
   whole (e.g. a package dropped from one seed but still pulled in by
   another shouldn't show as a net removal).
7. Print the diff as plain text with light markdown-ish styling (e.g.
   `**seedname**` headers, `+pkg` / `-pkg` lines) to stdout — this format
   needs to be plain enough to paste directly into a Launchpad MP comment,
   which supports no rich formatting.

## Output format

Plain text: a global diff section first, then one section per seed, only
for seeds with changes. A seed that appears or disappears entirely between
the two refs should be labelled as such rather than left to be inferred
from an all-added/all-removed package list. Rough shape:

```
**global**
+newpackage
-oldpackage

**desktop**
+newpackage
-oldpackage

**server** (new seed)
+anotherpackage
+yetanotherpackage

**oldseed** (removed seed)
-somepackage
```

## Exit codes

- `0`: ran successfully, regardless of whether any diffs were found.
- nonzero: germinate or tool failure.

This distinction matters for the future Launchpad wrapper, which needs to
tell "ran fine, no changes" apart from "broke."

## Scope for v1

In scope:
- Local CLI operation as described above.
- Per-seed expanded-package-list diffing only (no justification/dependency
  chain diffing — germinate's "why" output is likely too noisy for MP
  comments and is deferred).
- Support for testing changes to either an outer seed collection or the
  platform collection (one at a time).
- Graceful handling of a seed appearing or disappearing between the two
  refs (labelled explicitly, not just shown as all-added/all-removed).
- A global diff (union across all seeds) in addition to per-seed diffs.

Out of scope for v1:
- Launchpad integration itself. The eventual design is a separate wrapper
  that resolves a Launchpad MP's source/target branches to git refs,
  invokes `germinate-diff` as a subprocess, captures stdout, and posts it
  as an MP comment via launchpadlib. The CLI contract above (args in,
  plain text on stdout, exit code signals success/failure) is designed to
  make that wrapper a thin layer.
- Generic `STRUCTURE`/remote resolution for dependent seed collections —
  using a hardcoded map instead.
- Handling simultaneous proposed changes to more than one seed collection
  at once (e.g. platform and desktop both changing at once).

## Open questions (to resolve during implementation)

1. **How `STRUCTURE`-relative paths get resolved by germinate.** The
   maintainer recalls this involves "a series of obscure hacks" in
   germinate and wants to consult the germinate source directly before
   deciding exactly how the hardcoded collection map should interact with
   it — e.g. whether germinate needs to be told explicitly where to find a
   dependent collection, or whether it resolves this on its own once the
   local checkout exists at the expected relative path.
2. **Exact germinate invocation.** The maintainer knows this from shell
   history, not from memory, and will supply the actual command-line
   invocation (flags, `--apt-config` usage, JSON output flag, etc.) during
   implementation.
3. **Options that don't affect the core design** — e.g. where the
   hardcoded collection map lives (config file vs. inline dict), chdist
   base directory handling — can be decided naturally during
   implementation.

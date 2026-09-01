# germinate-diff

Show the consequences of a proposed change to an Ubuntu seed collection.

Given two refs of a seed git repo, germinate-diff runs germinate against each
and diffs the resulting expanded per-seed package lists.

```
germinate-diff <seed-repo> <old-ref> <new-ref> <chdist-name> [options]
```

Both runs use the same archive metadata (your existing `chdist`) and the same
checkouts of any other seed collections involved, so the diff reflects only
the seed change and not archive drift.

Output is plain text with light markdown-ish styling, plain enough to paste
straight into a Launchpad merge proposal comment:

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

The global section comes first and is the union of expanded packages across
all seeds, old versus new. It is computed from the two unions rather than by
summing the per-seed diffs, so a package dropped from one seed but still
pulled in by another does not show as a net removal — it shows as a per-seed
change under an explicitly empty global section.

Per-seed sections follow, one for each seed whose expanded list changed. A
seed that exists in only one of the two runs is not an error: its list is
treated as empty on the side where it does not exist, and it is labelled `(new
seed)` or `(removed seed)` so an all-added or all-removed list does not have
to be interpreted as one.

Exit status is 0 whenever both germinate runs succeeded, whether or not any
differences were found, and nonzero if germinate or the tool itself failed.

## Requirements

* Python 3.6 or later; no third-party modules.
* `germinate` on `$PATH` (or named with `--germinate`).
* `git`.
* A `chdist` for the series you are germinating against, as created by
  `chdist create` from devscripts.

## Setting up

### A chdist

germinate-diff does not fetch or pin an archive snapshot. It uses an existing
per-series chdist through germinate's `--apt-config`:

```
chdist create questing
$EDITOR ~/.chdist/questing/etc/apt/sources.list
chdist apt-get questing update
```

Pass the chdist name as the fourth argument. Both runs use it, so the archive
is identical on each side. `--chdist-base` overrides where chdists are looked
for (default `$CHDIST_HOME`, or `~/.chdist`); the argument may also be a path
to a chdist directory or straight to an `apt.conf`.

### The collection map

Seed collections build on each other: an outer collection such as desktop or
server pulls in the platform collection through an `include` line in its
`STRUCTURE` file. Only the collection under test should vary between the two
runs, so every other collection is taken from a fixed local checkout listed in
a small hand-maintained map:

```ini
# ~/.config/germinate-diff/collections.conf
[collections]
platform.questing = ~/seeds/platform
ubuntu.questing = ~/seeds/ubuntu
```

Keys are branch names exactly as they appear in `include` lines (and as
germinate's `--seed-dist`); values are local checkouts, with relative paths
resolved against the config file's directory. See `examples/collections.conf`.

The map is read from `$XDG_CONFIG_HOME/germinate-diff/collections.conf` or
`/etc/germinate-diff/collections.conf`, whichever exists first;
`--collection-map FILE` uses a different file, and `--collection BRANCH=DIR`
adds or overrides single entries without editing anything.

If the collection under test needs a collection the map does not cover,
germinate-diff says which ones are missing and who included them, rather than
letting germinate fail with a bare "could not open STRUCTURE".

A collection with no `include` lines — the platform collection itself, say —
needs no map at all.

## Examples

Diff a proposed change to an outer collection:

```
germinate-diff ~/seeds/ubuntu main my-branch questing
```

Diff a change to the platform collection itself, with the map supplied inline:

```
germinate-diff ~/seeds/platform HEAD~1 HEAD questing \
    --collection platform.questing=~/seeds/platform
```

Keep the worktrees and germinate's own output around to look at:

```
germinate-diff ~/seeds/ubuntu main my-branch questing -v --work-dir /tmp/gd
```

## How it works

1. Two detached git worktrees of the seed repo are created, one at each ref.
2. For each side, a seed source directory is built: a directory of symlinks,
   one named after the branch under test pointing at that side's worktree, and
   one per entry in the collection map pointing at its fixed checkout.
3. Germinate is run once per side with `--seed-source` pointing at that
   directory, `--seed-dist` naming the branch under test, and `--apt-config`
   pointing at the chdist. Each run gets its own output directory, since
   germinate writes its output files into the current directory.
4. Each seed's expanded package list is read from the `<seed>.json` file
   germinate writes, and the seed names from the merged `structure` file.
5. The lists are diffed per seed and in the union across seeds, and the result
   is printed.

### How the collection map meets germinate

This was the one part of the design that needed germinate's source read rather
than guessed at. Germinate resolves a seed collection as
`<seed-source>/<branch>/`, and an `include <branch>` line in a `STRUCTURE`
file names another branch resolved against *the same* seed sources — see
`SeedStructure._parse` and `Seed._open_seed_url` in `germinate/seeds.py`. With
no `--vcs` option a seed source is a plain directory rather than something to
fetch, and with several seed sources the first one that has a branch wins.

So germinate needs no telling where a dependent collection lives, as long as
one exists at the path it expects. Building a directory of symlinks and
pointing `--seed-source` at it is enough: the branch under test resolves to a
worktree, everything it includes resolves to a fixed checkout, and germinate
does the rest. If a map entry has the same name as the collection under test —
when testing the platform collection itself, say — the worktree wins.

`tests/test_germinate_integration.py` checks this against germinate itself
where it is installed, so a change to how germinate resolves seeds shows up as
a test failure rather than a confusing diff.

### What is diffed

The expanded list of a seed is what germinate writes to `<seed>.json`: the
seed's own entries plus everything reached through its dependencies, excluding
anything already provided by a seed it inherits from.

Germinate's `all.json` is not used for the global diff, because it also
includes build-dependencies; the global diff is the union of the per-seed
expanded lists, as specified.

Germinate's `extra` pseudo-seed — packages built by seeded sources that landed
in no seed — is left out by default, since it is a residue rather than a seed
and mostly restates changes already shown. `--include-extra` adds it.

Reverse-dependency calculation is turned off by default (germinate's
`--no-rdepends`), because it writes a file per package and does not affect the
expanded lists being diffed. `--rdepends` turns it back on.

Justification ("why") diffing is deliberately not implemented; it is too noisy
for an MP comment.

## Running the tests

```
python3 -m unittest discover
```

The tests do not need an archive: most of them drive the CLI against a
stand-in germinate in `tests/fake_germinate.py`. The tests in
`tests/test_germinate_integration.py` exercise real germinate — its option
parser and its seed resolution — and are skipped when germinate is not
importable.

## Launchpad integration

Out of scope here. The intended shape is a separate wrapper that resolves a
merge proposal's source and target branches to git refs, invokes
`germinate-diff` as a subprocess, captures stdout, and posts it as an MP
comment via launchpadlib. The CLI contract — arguments in, plain text on
stdout, exit status distinguishing "ran fine" from "broke" — is meant to keep
that wrapper thin. Note that germinate itself logs to stdout;
germinate-diff captures that and keeps its own stdout to the diff alone.

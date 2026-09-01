# germidiff

Show the consequences of a proposed change to an Ubuntu seed collection.

Given two refs of a seed git repo, germidiff runs germinate against each
and diffs the resulting expanded per-seed package lists.

```
germidiff <seed-repo> <old-ref> <new-ref> <chdist-name> [options]
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
and 12 more, already pulled in by other seeds

**oldseed** (removed seed)
62 packages, all still pulled in by other seeds
```

The global section comes first and is the union of expanded packages across
all seeds, old versus new. It is computed from the two unions rather than by
summing the per-seed diffs, so a package dropped from one seed but still
pulled in by another does not show as a net removal — it shows as a per-seed
change under an explicitly empty global section.

Per-seed sections follow, one for each seed whose expanded list changed. A
seed that exists in only one of the two runs is not an error; it is labelled
`(new seed)` or `(removed seed)`.

Such a seed is summarised rather than listed in full. Its "diff" is its whole
expansion rather than a change to one, and most of that is noise: the packages
are in the archive either way, and only the seed accounting for them moved.
Removing a seed of 62 packages that are all still pulled in elsewhere is worth
one line, not 62. So the section leads with the packages that genuinely
entered or left the archive — the ones that also appear under `global` — and
counts the rest. `--whole-seed-lists` restores the full listing.

Last comes the retention check, described below, which reports packages the
change stopped seeding but did not actually remove.

Exit status is 0 whenever both germinate runs succeeded, whether or not any
differences were found, and nonzero if germinate or the tool itself failed.

## What the diff cannot show: retention

A change can leave every expanded list alone and still matter. Dropping a seed
entry often changes nothing, because something else in the archive pulls the
package in anyway — but "anyway" may mean a `Recommends`, which nobody outside
the seeds has promised to keep. The diff says "no changes" and the fact that
the package's presence became accidental goes unremarked.

So every run also reports what the change stopped naming but did not remove:

```
**no longer seeded, still pulled in**
! build-essential: only by dpkg-dev (Recommends)
  5 others held by hard dependencies: dpkg-dev, g++, gcc, libc6-dev, make
```

A `!` line means every reason germinate gave for keeping that package is a
`Recommends`: drop it upstream and the package leaves. Packages held by a real
dependency are counted on one line as reassurance, not as a warning. The
section is omitted when a change names nothing new and drops nothing.

When nothing is held softly the section says so in its own words, rather than
printing the same thing minus the `!` lines:

```
**no longer seeded, still pulled in**
all 5 held by hard dependencies: dpkg-dev, g++, gcc, libc6-dev, make
```

That matters because seeding the flagged package again is how you fix the
problem, and the fixed report has to be tellable from the broken one at a
glance.

This is cheap and scoped to the change's own footprint. Germinate writes each
seed's explicit entries to `<seed>.seed`, so "what did this change stop saying
out loud" is a set difference over data both runs already produced — typically
a handful of packages rather than the whole closure — and the reason comes
from germinate's own `Why` column.

### Probing it

Germinate records only *one* reason per package, so a `!` line is a strong
hint rather than proof, and it says nothing about what else would follow the
package out. `--probe-retention` settles both by germinating again with that
one dependency removed:

```
**retention probe**
cutting dpkg-dev's Recommends of build-essential removes 5 package(s):
-build-essential
-g++
-g++-15
-g++-15-x86-64-linux-gnu
-g++-x86-64-linux-gnu
```

That last part is the bit the cheap check cannot reach: `g++` is held by
`build-essential` through a hard dependency, so it looks solid until the root
is cut.

It costs one germination per flagged package plus one for the baseline, which
is why it is off by default. It needs germinate importable by the same Python,
since there is no way to ask the germinate command line to ignore a single
dependency; it goes through germinate's documented `Archive` interface,
rewriting one field as the archive streams past, and touches no private state.
Before reporting anything it checks that germinating in-process reproduces the
run being explained, and refuses rather than answering a question about a
different germination.

## Requirements

* Python 3.6 or later; no third-party modules.
* `germinate` on `$PATH` (or named with `--germinate`).
* `git`.
* A `chdist` for the series you are germinating against, as created by
  `chdist create` from devscripts.

## Setting up

### A chdist

germidiff does not fetch or pin an archive snapshot. It uses an existing
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

### Components

Which archive components are in play is a property of the chdist, not of
germidiff. Germinate ignores `--components` when given `--apt-config` and
reads whatever indexes apt has, so germidiff never passes it.

This matters because the platform and ubuntu collections are germinated
against main and restricted, while flavours use every component. Nothing here
knows which collection wants which — you choose by naming the right chdist, so
keep one per component set:

```
chdist create stonking          # deb ... stonking main restricted
chdist create stonking-all      # deb ... stonking main restricted universe multiverse
```

Getting it wrong does not fail. Germinating the platform collection against an
all-components chdist quietly pulled 12 universe and multiverse packages
(`ipmitool`, `isc-dhcp-server`, `amtterm`, …) into its closure and took `extra`
from 2124 packages to 7144. `-v` reports the components actually used, which is
the cheapest way to catch it:

```
germidiff: archive metadata: ~/.chdist/stonking/etc/apt/apt.conf (amd64, main restricted)
```

### Architecture

The architecture is taken from the chdist (its `APT::Architecture`) rather
than assumed, because germinating against an architecture the chdist does not
carry does not fail — it quietly produces a plausible-looking but meaningless
diff, built from whichever Packages files apt does have. `--arch` overrides
it, and germidiff then checks the architecture against the chdist's
`APT::Architectures` and warns if it is not there. That check has to happen
before germinating: afterwards the two are indistinguishable, since a
wrong-architecture run of a real collection resolves about as many packages,
and reports about as many problems, as a good one.

### The collection map

Seed collections build on each other: an outer collection such as desktop or
server pulls in the platform collection through an `include` line in its
`STRUCTURE` file. Only the collection under test should vary between the two
runs, so every other collection is taken from a fixed local checkout listed in
a small hand-maintained map:

```ini
# ~/.config/germidiff/collections.conf
[collections]
platform.questing = ~/seeds/platform
ubuntu.questing = ~/seeds/ubuntu
```

Keys are branch names exactly as they appear in `include` lines (and as
germinate's `--seed-dist`); values are local checkouts, with relative paths
resolved against the config file's directory. See `examples/collections.conf`.

The map is read from `$XDG_CONFIG_HOME/germidiff/collections.conf` or
`/etc/germidiff/collections.conf`, whichever exists first;
`--collection-map FILE` uses a different file, and `--collection BRANCH=DIR`
adds or overrides single entries without editing anything.

If the collection under test needs a collection the map does not cover,
germidiff says which ones are missing and who included them, rather than
letting germinate fail with a bare "could not open STRUCTURE".

A collection with no `include` lines — the platform collection itself, say —
needs no map at all.

## Examples

Diff a proposed change to an outer collection:

```
germidiff ~/seeds/ubuntu main my-branch questing
```

Diff a change to the platform collection itself, with the map supplied inline:

```
germidiff ~/seeds/platform HEAD~1 HEAD questing \
    --collection platform.questing=~/seeds/platform
```

Keep the worktrees and germinate's own output around to look at:

```
germidiff ~/seeds/ubuntu main my-branch questing -v --work-dir /tmp/gd
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
for an MP comment — a single package's reverse-dependency tree from one small
germination ran to 3491 lines. The retention check above is the useful part of
that idea, scoped to the packages the change actually touched.

The expanded lists come from germinate's JSON output, but germinate records
*why* a package is present only in its human-readable tables, so the retention
check parses those. It degrades to reporting no reason rather than failing, so
a change to that format can cost the retention check but never the diff.

## Running the tests

```
python3 -m unittest discover
```

The tests do not need an archive or a network: most of them drive the CLI
against a stand-in germinate in `tests/fake_germinate.py`. The tests in
`tests/test_germinate_integration.py` exercise real germinate — its option
parser and its seed resolution — and are skipped when germinate is not
importable.

For a check against a real archive, point it at a real chdist:

```
chdist create stonking
printf 'deb http://archive.ubuntu.com/ubuntu/ stonking main restricted\n' \
    > ~/.chdist/stonking/etc/apt/sources.list
printf 'deb-src http://archive.ubuntu.com/ubuntu/ stonking main restricted\n' \
    >> ~/.chdist/stonking/etc/apt/sources.list
chdist apt stonking update

germidiff ~/seeds/ubuntu main my-branch stonking
```

## Launchpad integration

Out of scope here. The intended shape is a separate wrapper that resolves a
merge proposal's source and target branches to git refs, invokes
`germidiff` as a subprocess, captures stdout, and posts it as an MP
comment via launchpadlib. The CLI contract — arguments in, plain text on
stdout, exit status distinguishing "ran fine" from "broke" — is meant to keep
that wrapper thin. Note that germinate itself logs to stdout;
germidiff captures that and keeps its own stdout to the diff alone.

## Licence

Copyright (C) 2026 Canonical Ltd.

germidiff is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 3, as published by the Free
Software Foundation. There is no "or later" option; see [COPYING](COPYING) for
the full text.

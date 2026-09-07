# germidiff

Show the consequences of a proposed change to an Ubuntu seed collection.

Given two refs of a seed git repo, germidiff runs germinate against each
and diffs the resulting expanded per-seed package lists.

```
germidiff <seed-repo> <old-ref> <new-ref> <chdist-name> [options]
```

Both runs use the same archive metadata (a `chdist`, kept in a directory of
germidiff's own) and the same checkouts of any other seed collections
involved, so the diff reflects only the seed change and not archive drift.

To diff a Launchpad merge proposal, and let germidiff work out the refs, the
collections and the chdist for itself:

```
germidiff-mp <merge-proposal-url> [options]
```

See [Diffing a merge proposal](#diffing-a-merge-proposal) below.

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

The global section comes first — below the metapackage note described later,
where there is one — and is the union of expanded packages across all seeds,
old versus new. It is computed from the two unions rather than by summing the
per-seed diffs, so a package dropped from one seed but still pulled in by
another does not show as a net removal — it shows as a per-seed change under
an explicitly empty global section.

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

A seed affected only through what it inherits is reported in its own place in
that order, marked as such; the next section says why. Last comes the retention
check, which reports packages the change stopped seeding but did not actually
remove.

### What a seed contains, as against what it accounts for

Germinate lists a package only in the seed that *first* pulls it in, so a
seed's section says what it newly accounts for, not what it holds. A seed can
therefore lose a package by losing it from something it inherits, without that
showing anywhere in its own section — and "would an image built from this seed
still have curl?" is not a question the per-seed sections answer.

So where those differ, the seed's section reports what it *contains* instead,
marked as such:

```
**server-minimal** (including inherited seeds)
-curl
-libcurl4t64
-pollinate
...
```

Each seed appears once. Where what it holds changed differently from what it
accounts for, that is the section you get, because showing both would say the
same seed gained and lost packages in the same breath — true, and unreadable.
A seed the change added or removed is never reported this way, since it gains
or loses everything it inherits by definition and its label already says so.

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

### Metapackage lag

A seed change does not reach the archive on its own. `ubuntu-meta` and its
siblings are built by `germinate-update-metapackage`, which sets each
metapackage's `Depends` from the explicit entries of the seed it stands for,
and its `Recommends` from that seed's `seed-recommends` entries — in both cases
together with any seed named in that seed's `Task-Seeds:` header. Those
metapackages then sit in the archive that the *next* germination reads.

So dropping a seed entry often looks like nothing happened — the package is
still pulled in, by a metapackage built from the previous state of these very
seeds. germidiff spots those dependencies.

It is not a guess. A holder counts only when it is named by the seed's
`Task-Metapackage:` header, or its name ends in `-<seed>` *and* it is itself an
entry of that seed; and only when the package was named by that seed before the
change and is not after — which is exactly the condition under which
regeneration drops the dependency.

Both relationships count, and a seed's two entry lists are read as one. A stale
`Recommends` hides a change exactly as completely as a stale `Depends`, since
germinate follows both; but a package moved from a seed's entries to its
`seed-recommends` has not been dropped at all — it comes back as a `Recommends`
— so that is no edge, and the probe below cuts whichever relationship the
archive actually has.

Having found those, germidiff germinates the new side again without them, and
diffs against *that* — so there is one diff, saying what the change does rather
than what it does not do yet. A note above it says what was assumed:

```
**assuming the metapackages are rebuilt**
pollinate dropped from ubuntu-cloud-minimal, ubuntu-server,
ubuntu-server-minimal, which are built from these seeds

**global**
-pollinate

**server-cloud-minimal**
-curl
-libcurl4t64
...
```

This runs by default, but only when there is something for it to say;
`--no-metapackage-probe` turns it off and reports the archive as it stands,
warning about each dependency being taken at face value. It is a probe like
the retention one below, so it needs germinate importable by the same Python,
and falls back to those same warnings where it is not.

### Probing it

Germinate records only *one* reason per package, so a `!` line is a strong
hint rather than proof, and it says nothing about what else would follow the
package out. `--probe-retention` settles both by germinating again with that
one dependency removed:

```
**retention probe**
without dpkg-dev's Recommends on build-essential:

**global**
-build-essential
-g++
-g++-15
-g++-15-x86-64-linux-gnu
-g++-x86-64-linux-gnu
```

What follows the cut is a diff in the same shape as the main one — global
first, then a section per affected seed — because it answers the same
question, and a cut that changes nothing says `nothing changes` instead.

The per-seed part is the bit the cheap check cannot reach: `g++` is held by
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
* `germinate` on `$PATH` (or named with `--germinate`), and importable by
  that same Python for the probes — the metapackage one that runs by default
  as well as `--probe-retention`.
* `git`.
* A `chdist` for the series you are germinating against, as created by
  `chdist create` from devscripts, in germidiff's own chdist directory.
  `germidiff-mp` will create one itself.
* For `germidiff-mp` only: `chdist` on `$PATH`, and network access to
  Launchpad and the archive.

## Setting up

### A chdist

germidiff does not fetch or pin an archive snapshot. It reads the archive
through a per-series chdist, handed to germinate as `--apt-config`.

Chdists live in a directory of germidiff's own, `~/.cache/germidiff/chdists`
(`$XDG_CACHE_HOME/germidiff/chdists`), and *not* in the `~/.chdist` the
`chdist` tool keeps for you. `germidiff-mp` creates and updates chdists named
after the series it is germinating — exactly the names you would have picked
by hand — so sharing a directory would mean it either refused to run because
yours carries the wrong components, or ran `apt-get update` on one you were
deliberately holding still. `$CHDIST_HOME` is ignored for the same reason.

To make one for `germidiff` itself:

```
chdist -d ~/.cache/germidiff/chdists create questing
$EDITOR ~/.cache/germidiff/chdists/questing/etc/apt/sources.list
chdist -d ~/.cache/germidiff/chdists apt-get questing update
```

Pass the chdist name as the fourth argument. Both runs use it, so the archive
is identical on each side.

To use a chdist you already have, pass its path rather than its name — the
fourth argument may be a chdist directory or an `apt.conf` — or move the whole
search with `--chdist-base ~/.chdist`, which both commands accept:

```
germidiff ~/seeds/ubuntu HEAD~1 HEAD ~/.chdist/questing
germidiff ~/seeds/ubuntu HEAD~1 HEAD questing --chdist-base ~/.chdist
```

### Components

Which archive components are in play is a property of the chdist, not of
germidiff. Germinate ignores `--components` when given `--apt-config` and
reads whatever indexes apt has, so germidiff never passes it.

This matters because the platform and ubuntu collections are germinated
against main and restricted, while flavours use every component. `germidiff`
itself does not know which collection wants which — you choose by naming the
right chdist, so keep one per component set. `germidiff-mp` does know, and
picks the chdist accordingly — see below.

```
chdist -d ~/.cache/germidiff/chdists create stonking      # deb ... stonking main restricted
chdist -d ~/.cache/germidiff/chdists create stonking-all  # deb ... stonking main restricted universe multiverse
```

Getting it wrong does not fail. Germinating the platform collection against an
all-components chdist quietly pulled 12 universe and multiverse packages
(`ipmitool`, `isc-dhcp-server`, `amtterm`, …) into its closure and took `extra`
from 2124 packages to 7144. `-v` reports the components actually used, which is
the cheapest way to catch it:

```
germidiff: archive metadata: ~/.cache/germidiff/chdists/stonking/etc/apt/apt.conf (amd64, main restricted)
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

### Dependent collections

Seed collections build on each other: an outer collection such as desktop or
server pulls in the platform collection through an `include` line in its
`STRUCTURE` file. Only the collection under test should vary between the two
runs, so every other collection is taken from a fixed local checkout.

There is nothing to configure. A branch is looked for beside the seed repo
under test, in a directory named after it — the layout seed branches are
normally checked out in, and the same one germinate resolves a seed source
against. With `/tmp/ubuntu.stonking` and `/tmp/platform.stonking` side by side,
this is the whole command:

```
germidiff /tmp/ubuntu.stonking HEAD~1 HEAD stonking
```

In practice that is the whole of it: what an outer collection includes is the
platform collection, which anyone editing seeds locally already has checked
out. `germidiff-mp` needs no checkouts at all, because it clones what a
proposal needs into a cache laid out the same way.

A branch name may itself be a path: `include ubuntu.stonking/languages` names a
collection nested inside `ubuntu.stonking`. Those arrive with the collection
that contains them and need nothing said about them either.

If something is missing, germidiff says which collections they are and who
included them, rather than letting germinate fail with a bare "could not open
STRUCTURE". A collection with no `include` lines — the platform collection
itself, say — needs nothing beside it.

## Examples

Diff a proposed change to an outer collection:

```
germidiff ~/seeds/ubuntu main my-branch questing
```

Diff a change to the platform collection itself, which includes nothing and
so needs nothing beside it:

```
germidiff ~/seeds/platform.questing HEAD~1 HEAD questing
```

Keep the worktrees and germinate's own output around to look at:

```
germidiff ~/seeds/ubuntu main my-branch questing -v --work-dir /tmp/gd
```

## How it works

1. Two detached git worktrees of the seed repo are created, one at each ref.
2. For each side, a seed source directory is built: a directory of symlinks,
   one named after the branch under test pointing at that side's worktree, and
   one per included collection pointing at its checkout beside the repo.
3. Germinate is run once per side with `--seed-source` pointing at that
   directory, `--seed-dist` naming the branch under test, and `--apt-config`
   pointing at the chdist. Each run gets its own output directory, since
   germinate writes its output files into the current directory.
4. Each seed's expanded package list is read from the `<seed>.json` file
   germinate writes, and the seed names from the merged `structure` file.
5. The lists are diffed per seed and in the union across seeds, and the result
   is printed.

### How that meets germinate

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
does the rest. The collection under test always wins over a checkout of the
same name beside the repo — which is what testing the platform collection
itself amounts to, since it then sits among its own siblings.

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
base=~/.cache/germidiff/chdists
mkdir -p "$base"
chdist -d "$base" create stonking
printf 'deb http://archive.ubuntu.com/ubuntu/ stonking main restricted\n' \
    > "$base"/stonking/etc/apt/sources.list
printf 'deb-src http://archive.ubuntu.com/ubuntu/ stonking main restricted\n' \
    >> "$base"/stonking/etc/apt/sources.list
chdist -d "$base" apt-get stonking update

germidiff ~/seeds/ubuntu main my-branch stonking
```

## Diffing a merge proposal

`germidiff-mp` takes a Launchpad merge proposal and does the rest:

```
germidiff-mp https://code.launchpad.net/~gjolly/ubuntu-seeds/+git/ubuntu/+merge/509106
```

It reads the proposal, works out what to germinate, fetches it, makes sure
there is a chdist to read the archive from, and writes the report to stdout.
Nothing is posted back to Launchpad.

Two lines above the report say where it came from, since a report pasted
somewhere else is otherwise a list of package names with nothing to anchor it:

```
germidiff of https://code.launchpad.net/~gjolly/ubuntu-seeds/+git/ubuntu/+merge/509106
ubuntu.resolute, 0f3a1c9e4b27..a51d8c0f6e94, against resolute (main restricted)
```

`--no-header` leaves them off.

The proposal answers most of the questions. The *target* repository names the
collection — a proposal arrives from a fork that may be called anything, but
what it proposes to change is the target — and the target branch names the
series, so a proposal against `+git/ubuntu` at `refs/heads/resolute` is a
change to `ubuntu.resolute`.

Three things are worth knowing about the rest.

**It diffs from the merge base**, not from the target's tip. Anything that
landed on the target while the proposal waited is not part of what the
proposal does, and reporting it as though it were would blame this change for
someone else's packages. That is also the comparison Launchpad's own diff
shows, so the report and the diff beside it describe the same change.

**Included collections are cloned into a reusable cache**, `~/.cache/germidiff/seeds`
by default, laid out as `platform.resolute` and so on — the same layout seed
branches are usually checked out in, and the one germidiff already looks in
for a collection's siblings. Both sides of the change get a say in what is
needed, since adding or dropping an `include` line is an ordinary seed change.
Reusing the directory is the point: running over a stream of proposals should
pay for the seed history once.

**The chdist is named for the series and its components.** The `ubuntu` and
`platform` collections germinate against main and restricted, and use a chdist
named for the series alone; flavours germinate against the whole archive, from
one named `SERIES-all`. A missing chdist is created and updated, under
germidiff's own chdist directory rather than your `~/.chdist`, so that a
chdist of yours with the same name is neither read nor touched. An existing
one whose components do not match is *refused*, because germinate ignores
`--components` when it is given an `--apt-config` — the chdist alone decides
what a run can see, so germinating the platform seeds against a chdist
carrying universe quietly resolves into packages that are not there for them
and produces a plausible report that is wrong. `--no-check-components` goes
ahead anyway, and `--chdist NAME` takes one exactly as it stands.

`--dry-run` fetches everything and prints what it would germinate, which is
the cheap way to check the resolution before paying for two germinations.

Reads go straight to the Launchpad API over HTTP rather than through
launchpadlib: this needs three fields of one public object, and anonymous
reads have no use for an OAuth stack. (launchpadlib also fails outright behind
an HTTP proxy that plain requests get through.)

### Posting

Still out of scope, and deliberately separate: resolving a proposal needs no
credentials and can be run against anything, while commenting on one needs an
account and is hard to take back. Launchpad has no API for editing or deleting
a merge proposal comment, so anything that posts automatically needs to
recognise its own previous comment and stay quiet when the report has not
changed — otherwise a branch pushed five times collects five identical walls
of package names.

## Licence

Copyright (C) 2026 Canonical Ltd.

germidiff is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 3, as published by the Free
Software Foundation. There is no "or later" option; see [COPYING](COPYING) for
the full text.

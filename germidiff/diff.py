"""Diffing expanded package lists between two germinate runs."""

# Copyright (C) 2026 Canonical Ltd.
#
# germidiff is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as
# published by the Free Software Foundation.
#
# germidiff is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with germidiff; see the file COPYING.  If not, see
# <https://www.gnu.org/licenses/>.

__all__ = ["SeedDiff", "SeedContents", "Diff", "diff_runs"]

#: A seed present in both runs.
UNCHANGED_PRESENCE = None
#: A seed that only the new run has.
NEW_SEED = "new"
#: A seed that only the old run has.
REMOVED_SEED = "removed"


class SeedDiff:
    """The change to one seed's expanded package list (or to the union)."""

    def __init__(
        self,
        name,
        added,
        removed,
        presence=UNCHANGED_PRESENCE,
        entered=None,
        left=None,
    ):
        self.name = name
        self.added = sorted(added)
        self.removed = sorted(removed)
        #: The subset of :attr:`added` that also entered the archive, and of
        #: :attr:`removed` that also left it, rather than merely changing
        #: which seed accounts for them.  Only meaningful for a seed the
        #: change added or removed, where the lists are whole expansions
        #: rather than deltas; :func:`diff_runs` narrows them there.  They
        #: default to the whole lists so that anything which does not narrow
        #: them reports more rather than wrongly claiming a package was
        #: already accounted for elsewhere.
        self.entered = self.added if entered is None else sorted(entered)
        self.left = self.removed if left is None else sorted(left)
        #: ``NEW_SEED``, ``REMOVED_SEED`` or ``None`` if the seed exists in
        #: both runs.  A seed that appears or disappears is not an error: its
        #: list is treated as empty on the side where it does not exist, and
        #: it is labelled so that an all-added or all-removed list does not
        #: have to be interpreted as one.
        self.presence = presence

    @property
    def elsewhere(self):
        """How many packages only changed which seed accounts for them."""
        return (len(self.added) - len(self.entered)) + (
            len(self.removed) - len(self.left)
        )

    @property
    def changed(self):
        """Whether this seed is worth showing.

        A seed that appears or disappears is worth reporting even if its
        expanded list is empty, because its existence is itself the change.
        """
        return bool(self.added or self.removed or self.presence)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<SeedDiff %s +%d -%d%s>" % (
            self.name,
            len(self.added),
            len(self.removed),
            " (%s)" % self.presence if self.presence else "",
        )


class SeedContents:
    """The change to what a seed *contains*, inherited seeds included.

    The per-seed diffs say what each seed newly accounts for, since germinate
    lists a package only in the seed that first pulls it in.  That answers
    "which seed is this package here for", but not "would an image built from
    this seed still have it" -- a package can leave a seed's contents by
    leaving something it inherits, without appearing in its own diff at all.

    Only reported where the two differ; otherwise the seed's own section has
    already said it.
    """

    def __init__(self, name, lost, gained):
        self.name = name
        self.lost = sorted(lost)
        self.gained = sorted(gained)

    @property
    def changed(self):
        return bool(self.lost or self.gained)


class Diff:
    """A whole comparison: the global diff plus the per-seed diffs."""

    def __init__(self, global_diff, seeds, contents=()):
        self.global_diff = global_diff
        #: Every seed's diff, in report order, changed or not.
        self.seeds = seeds
        #: What each seed contains, for the seeds where that changed.
        self.contents = list(contents)

    @property
    def changed_seeds(self):
        return [seed for seed in self.seeds if seed.changed]

    @property
    def changed(self):
        return bool(
            self.global_diff.changed or self.changed_seeds or self.contents
        )


def _ordered_seed_names(old_run, new_run):
    """Seed names in report order.

    New-run order first, since that is the structure the change produces,
    then any seed the change removed, in the order the old run had it.
    """
    names = list(new_run.seed_names)
    seen = set(names)
    for name in old_run.seed_names:
        if name not in seen:
            names.append(name)
            seen.add(name)
    return names


def diff_runs(old_run, new_run, global_name="global"):
    """Diff two germinate runs.

    Returns a :class:`Diff` holding the global diff (the union of expanded
    packages across all seeds, old versus new) and one :class:`SeedDiff` per
    seed.  The global diff is computed from the unions rather than by summing
    the per-seed diffs, so a package dropped from one seed but still pulled in
    by another does not show up as a net removal.
    """
    seeds = []
    for name in _ordered_seed_names(old_run, new_run):
        old_packages = old_run.seeds.get(name)
        new_packages = new_run.seeds.get(name)
        if old_packages is None:
            presence = NEW_SEED
            old_packages = set()
        elif new_packages is None:
            presence = REMOVED_SEED
            new_packages = set()
        else:
            presence = UNCHANGED_PRESENCE
        seeds.append(
            SeedDiff(
                name,
                new_packages - old_packages,
                old_packages - new_packages,
                presence=presence,
            )
        )

    old_union = old_run.union()
    new_union = new_run.union()
    entered_archive = new_union - old_union
    left_archive = old_union - new_union
    global_diff = SeedDiff(global_name, entered_archive, left_archive)

    # For a seed the change added or removed, the "diff" is its entire
    # expanded list, which says little on its own: most of those packages are
    # in the archive either way and only their attribution moved.  Record
    # which ones genuinely came or went so the report can lead with those.
    for seed in seeds:
        if not seed.presence:
            continue
        seed.entered = sorted(set(seed.added) & entered_archive)
        seed.left = sorted(set(seed.removed) & left_archive)

    # Only where inheritance makes a difference: when a seed's contents
    # change exactly as its own list does, its own section already said so,
    # and repeating it is noise.
    contents = []
    for seed in seeds:
        if seed.presence:
            # A seed the change added or removed gains or loses everything
            # it inherits by definition; its label already says so.
            continue
        old_contents = old_run.inclusive(seed.name)
        new_contents = new_run.inclusive(seed.name)
        entry = SeedContents(
            seed.name,
            old_contents - new_contents,
            new_contents - old_contents,
        )
        if not entry.changed:
            continue
        if set(entry.lost) == set(seed.removed) and set(entry.gained) == set(
            seed.added
        ):
            continue
        contents.append(entry)

    return Diff(global_diff, seeds, contents=contents)

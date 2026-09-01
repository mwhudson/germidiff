"""Diffing expanded package lists between two germinate runs."""

__all__ = ["SeedDiff", "Diff", "diff_runs"]

#: A seed present in both runs.
UNCHANGED_PRESENCE = None
#: A seed that only the new run has.
NEW_SEED = "new"
#: A seed that only the old run has.
REMOVED_SEED = "removed"


class SeedDiff:
    """The change to one seed's expanded package list (or to the union)."""

    def __init__(self, name, added, removed, presence=UNCHANGED_PRESENCE):
        self.name = name
        self.added = sorted(added)
        self.removed = sorted(removed)
        #: ``NEW_SEED``, ``REMOVED_SEED`` or ``None`` if the seed exists in
        #: both runs.  A seed that appears or disappears is not an error: its
        #: list is treated as empty on the side where it does not exist, and
        #: it is labelled so that an all-added or all-removed list does not
        #: have to be interpreted as one.
        self.presence = presence

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


class Diff:
    """A whole comparison: the global diff plus the per-seed diffs."""

    def __init__(self, global_diff, seeds):
        self.global_diff = global_diff
        #: Every seed's diff, in report order, changed or not.
        self.seeds = seeds

    @property
    def changed_seeds(self):
        return [seed for seed in self.seeds if seed.changed]

    @property
    def changed(self):
        return bool(self.global_diff.changed or self.changed_seeds)


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
    global_diff = SeedDiff(
        global_name, new_union - old_union, old_union - new_union
    )

    return Diff(global_diff, seeds)

"""Metapackages generated from the seeds under test, and the lag they cause.

A seed change does not reach the archive on its own.  ``ubuntu-meta`` and its
siblings are built by ``germinate-update-metapackage``, which sets each
metapackage's ``Depends`` from the explicit entries of the seed it stands for,
and its ``Recommends`` from that seed's ``seed-recommends`` entries (in both
cases together with any seed named in the seed's ``Task-Seeds:`` header).
Those metapackages are then in the archive that the next germination reads.

So dropping a seed entry often looks like nothing happened: the package is
still pulled in, by a metapackage built from the *previous* state of these
same seeds.  The effect only lands once the metapackages are rebuilt.  This
module works out which of those dependencies are living on borrowed time.
"""

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

import os
import re

from germidiff.listfile import Reason, parse_list_file

__all__ = [
    "PendingEdge",
    "pending_edges",
    "seed_headers",
]

# The relationship a seed entry becomes in the generated metapackage, keyed
# by how germinate says the archive's metapackage holds the package today.
# Anything else -- a seed reason, a build-dependency -- is not a metapackage
# dependency that rebuilding would rewrite.
_FIELD_FOR_KIND = {
    Reason.DEPENDS: "Depends",
    Reason.RECOMMENDS: "Recommends",
}

_TASK_SEEDS = re.compile(r"^Task-Seeds:\s*(.*)", re.I)
_TASK_METAPACKAGE = re.compile(r"^Task-Metapackage:\s*(.*)", re.I)


class PendingEdge:
    """A metapackage dependency that regenerating the metapackages removes."""

    def __init__(self, metapackage, package, seed, field="Depends"):
        self.metapackage = metapackage
        self.package = package
        #: The seed the metapackage is built from, which stopped naming it.
        self.seed = seed
        #: Which relationship holds it: "Depends" or "Recommends".
        self.field = field

    def __eq__(self, other):
        if not isinstance(other, PendingEdge):
            return NotImplemented
        return (self.metapackage, self.package, self.field) == (
            other.metapackage,
            other.package,
            other.field,
        )

    def __hash__(self):
        return hash((self.metapackage, self.package, self.field))

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<PendingEdge %s -%s-> %s>" % (
            self.metapackage, self.field, self.package
        )


def seed_headers(out_dir, seedname):
    """Read a seed's ``Task-Seeds`` and ``Task-Metapackage`` headers.

    Germinate writes each seed's own text to ``<seed>.seedtext``, headers
    included, so these come from the run rather than from guessing at where
    the seed file lives.
    """
    task_seeds = []
    metapackage = None
    path = os.path.join(out_dir, "%s.seedtext" % seedname)
    try:
        with open(path, encoding="UTF-8", errors="replace") as f:
            for line in f:
                match = _TASK_SEEDS.match(line)
                if match is not None:
                    task_seeds = match.group(1).split()
                    continue
                match = _TASK_METAPACKAGE.match(line)
                if match is not None:
                    metapackage = match.group(1).strip()
    except OSError:
        pass
    return task_seeds, metapackage


def _entry_union(out_dir, seeds):
    """Everything a metapackage's seeds name, by either relationship.

    Both lists are taken together, because what matters is whether the
    rebuilt metapackage would mention the package at all.  A package moved
    from a seed's entries to its seed-recommends has not been dropped: it
    comes back as a Recommends, and reporting it as pending removal would be
    telling the reviewer their change does something it does not.
    """
    entries = set()
    for seed in seeds:
        for suffix in ("seed", "seed-recommends"):
            entries.update(
                parse_list_file(
                    os.path.join(out_dir, "%s.%s" % (seed, suffix))
                )
            )
    return entries


def _names_seed(metapackage, seed, out_dir, declared):
    """Whether ``metapackage`` is the one built from ``seed``.

    ``germinate-update-metapackage`` takes the name from a
    ``Task-Metapackage:`` header, and otherwise builds it as
    ``<flavour>-<seed>``.  The flavour prefix lives in ubuntu-meta's own
    configuration rather than in the seeds, so where there is no header this
    falls back to the name's shape -- but only together with the metapackage
    being seeded in that very seed, which is how these metapackages are
    kept in the archive.  Two independent signals rather than a guess.
    """
    if declared is not None:
        return metapackage == declared
    if not metapackage.endswith("-%s" % seed):
        return False
    return metapackage in _entry_union(out_dir, [seed])


def pending_edges(old_out_dir, new_out_dir, seed_names, retained):
    """Find dependencies that only regenerating the metapackages will remove.

    For each package the change stopped naming but which is still pulled in,
    check whether a holder is a metapackage built from a seed of this very
    collection, and whether that seed stopped naming the package too.  When
    both hold, the dependency exists only because the archive's metapackages
    predate this change.

    Both relationships count.  A metapackage's Recommends is generated from
    its seed's seed-recommends exactly as its Depends is generated from the
    seed's entries, and germinate follows Recommends, so an out-of-date
    Recommends hides a change just as completely as an out-of-date Depends.
    """
    edges = []
    for entry in retained:
        for reason in entry.reasons:
            holder = reason.holder
            field = _FIELD_FOR_KIND.get(reason.kind)
            if holder is None or field is None:
                continue
            for seed in seed_names:
                task_seeds, declared = seed_headers(new_out_dir, seed)
                if not _names_seed(holder, seed, new_out_dir, declared):
                    continue
                covered = [seed] + task_seeds
                was = _entry_union(old_out_dir, covered)
                now = _entry_union(new_out_dir, covered)
                if entry.package in was and entry.package not in now:
                    edge = PendingEdge(holder, entry.package, seed, field)
                    if edge not in edges:
                        edges.append(edge)
    return sorted(
        edges, key=lambda e: (e.package, e.metapackage, e.field)
    )

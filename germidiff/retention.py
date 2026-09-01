"""Why a package the change stopped seeding is still in the closure.

A seed change that removes entries often leaves the expanded lists alone,
because the packages it stopped naming are pulled in by something else.  That
is worth knowing rather than assuming: the retention may rest on a
``Recommends``, which nobody outside the seeds is obliged to keep.

The check is deliberately scoped to the change's own footprint.  Germinate
writes each seed's *explicit* entries to ``<seed>.seed`` and
``<seed>.seed-recommends``, so "what did this change stop naming out loud" is
a set difference over data both runs already produced -- typically a handful
of packages, not the whole closure.
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

import glob
import os

from germidiff.listfile import Reason, parse_list_file

__all__ = [
    "Retained",
    "find_retained",
    "soft_edges",
]


class Retained:
    """A package the change stopped naming that is still in the closure."""

    def __init__(self, package, reasons, seeds):
        self.package = package
        #: The distinct reasons germinate gave, across the seeds it landed in.
        self.reasons = reasons
        #: The seeds it now appears in.
        self.seeds = seeds

    @property
    def soft(self):
        """Whether every reason for keeping this package is a Recommends.

        If even one seed holds it through a hard dependency then dropping a
        Recommends somewhere cannot remove it, so there is nothing to warn
        about.
        """
        return bool(self.reasons) and all(r.soft for r in self.reasons)

    @property
    def holders(self):
        return sorted({r.holder for r in self.reasons if r.holder})

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Retained %s%s>" % (
            self.package,
            " (soft)" if self.soft else "",
        )


def explicit_entries(out_dir):
    """Every package a run's seeds named explicitly.

    Both plain seed entries and seed-recommends count as naming: either way
    the seed said the package should be there.
    """
    entries = set()
    for suffix in ("*.seed", "*.seed-recommends"):
        for path in glob.glob(os.path.join(out_dir, suffix)):
            entries.update(parse_list_file(path))
    return entries


def _reasons_in_run(out_dir, seed_names, packages):
    """Collect the reasons each package was given, per seed it landed in."""
    reasons = {pkg: [] for pkg in packages}
    seeds = {pkg: [] for pkg in packages}
    for seedname in seed_names:
        path = os.path.join(out_dir, seedname)
        if not os.path.isfile(path):
            continue
        entries = parse_list_file(path)
        for pkg in packages:
            reason = entries.get(pkg)
            if reason is not None:
                reasons[pkg].append(reason)
                seeds[pkg].append(seedname)
    return reasons, seeds


def find_retained(old_out_dir, new_out_dir, new_run):
    """Find packages the change stopped naming that survive anyway.

    Returns a list of :class:`Retained`, worst first: the ones held only by a
    Recommends before the rest, and alphabetical within each group.
    """
    stopped = explicit_entries(old_out_dir) - explicit_entries(new_out_dir)
    if not stopped:
        return []

    # Only those still in the closure; one the change actually removed shows
    # up in the diff itself and needs no explaining.
    still_here = {pkg for pkg in stopped if pkg in new_run.union()}
    if not still_here:
        return []

    reasons, seeds = _reasons_in_run(
        new_out_dir, new_run.seed_names, still_here
    )

    retained = []
    for pkg in still_here:
        distinct = []
        for reason in reasons[pkg]:
            if reason not in distinct:
                distinct.append(reason)
        retained.append(Retained(pkg, distinct, seeds[pkg]))

    retained.sort(key=lambda r: (not r.soft, r.package))
    return retained


def soft_edges(retained):
    """The distinct ``(holder, package)`` Recommends holding these packages.

    These are what a retention probe would cut, one at a time.  Deduplicated
    across packages, since one Recommends can be the only thing holding
    several of them.
    """
    edges = []
    for entry in retained:
        if not entry.soft:
            continue
        for reason in entry.reasons:
            if reason.kind != Reason.RECOMMENDS or not reason.holder:
                continue
            edge = (reason.holder, entry.package)
            if edge not in edges:
                edges.append(edge)
    return sorted(edges)

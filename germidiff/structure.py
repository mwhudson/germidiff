"""Minimal parsing of seed ``STRUCTURE`` files.

Germinate resolves a seed collection as ``<seed-base>/<branch>/STRUCTURE``,
and an ``include <branch>`` line inside a ``STRUCTURE`` file names another
branch to be resolved against the *same* list of seed bases (see
``germinate.seeds.SingleSeedStructure`` and ``SeedStructure._parse``).  We
parse just enough of that to work out which dependent collections a checkout
needs, so we can tell the user which ones are missing from the collection map
before handing anything to germinate.
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

__all__ = [
    "StructureError",
    "parse_structure",
    "required_branches",
    "seed_names_from_structure_output",
]


class StructureError(Exception):
    """A ``STRUCTURE`` file could not be read or made sense of."""


def parse_structure(path):
    """Parse a ``STRUCTURE`` file.

    Returns an ``(seed_order, includes)`` pair, where ``seed_order`` is the
    list of seed names declared in the file and ``includes`` is the list of
    branches named by ``include`` lines, both in file order.
    """
    seed_order = []
    includes = []
    try:
        with open(path, encoding="UTF-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        raise StructureError("could not read %s: %s" % (path, e))

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0].endswith(":"):
            seed_order.append(words[0][:-1])
        elif words[0] == "include":
            includes.extend(words[1:])
        # "feature" lines and anything unparseable are germinate's problem.
    return seed_order, includes


def required_branches(root_branch, resolve):
    """Walk ``include`` lines to find every branch a collection needs.

    ``resolve`` is called with a branch name and should return the local
    directory holding that branch's seeds, or ``None`` if it is unknown.
    Returns an ``(order, missing)`` pair: ``order`` lists the branches that
    were resolved (``root_branch`` first, then included branches in the order
    they were discovered), and ``missing`` lists the branches that could not
    be resolved, each as a ``(branch, included_by)`` pair.
    """
    order = []
    missing = []
    seen = set()
    queue = [(root_branch, None)]

    while queue:
        branch, included_by = queue.pop(0)
        if branch in seen:
            continue
        seen.add(branch)
        directory = resolve(branch)
        if directory is None:
            missing.append((branch, included_by))
            continue
        order.append(branch)
        _, includes = parse_structure(os.path.join(directory, "STRUCTURE"))
        for child in includes:
            if child not in seen:
                queue.append((child, branch))

    return order, missing


def seed_names_from_structure_output(path):
    """Read the seed names from the ``structure`` file germinate writes.

    Germinate writes the fully merged structure (the collection under test
    plus everything it includes) to a file called ``structure`` in its output
    directory, one ``seed: parents`` line per seed.  That is the authoritative
    list of the seeds a run produced, in inheritance order.
    """
    seed_order, _ = parse_structure(path)
    return seed_order

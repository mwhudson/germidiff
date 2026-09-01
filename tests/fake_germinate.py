#! /usr/bin/env python3
"""A stand-in for germinate, for testing germinate-diff without an archive.

It accepts the arguments germinate-diff passes, resolves seed branches the way
germinate does (``<seed-source>/<branch>/STRUCTURE``, following ``include``
lines), and writes the output files germinate-diff reads back: ``structure``
and one ``<seed>.json`` per seed.

"Expanding" a seed here means its own packages, plus a fake dependency for
any package written as ``name+dep`` in a seed file, minus anything already
provided by a seed it inherits from -- the same one-list-per-package rule
germinate follows.  That is enough to exercise everything on the
germinate-diff side without needing apt.
"""

import json
import optparse
import os
import sys


def parse_structure(path):
    seed_order = []
    inherit = {}
    includes = []
    with open(path, encoding="UTF-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            words = line.split()
            if words[0].endswith(":"):
                seed_order.append(words[0][:-1])
                inherit[words[0][:-1]] = words[1:]
            elif words[0] == "include":
                includes.extend(words[1:])
    return seed_order, inherit, includes


def collect(seed_base, branch, seen):
    """Merge a branch's structure with those of the branches it includes."""
    if branch in seen:
        return [], {}, []
    seen.add(branch)
    directory = os.path.join(seed_base, branch)
    structure = os.path.join(directory, "STRUCTURE")
    if not os.path.exists(structure):
        sys.stderr.write("? Could not open STRUCTURE for %s\n" % branch)
        sys.exit(1)
    seed_order, inherit, includes = parse_structure(structure)

    all_order = []
    all_inherit = {}
    branches = []
    for child in includes:
        child_order, child_inherit, child_branches = collect(
            seed_base, child, seen
        )
        all_order.extend(o for o in child_order if o not in all_order)
        all_inherit.update(child_inherit)
        branches.extend(b for b in child_branches if b not in branches)
    all_order.extend(o for o in seed_order if o not in all_order)
    all_inherit.update(inherit)
    if branch not in branches:
        branches.append(branch)
    return all_order, all_inherit, branches


def read_seed(seed_base, branches, name):
    for branch in reversed(branches):
        path = os.path.join(seed_base, branch, name)
        if os.path.exists(path):
            packages = []
            with open(path, encoding="UTF-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("*"):
                        packages.append(line[1:].strip())
            return packages
    return []


def main():
    parser = optparse.OptionParser()
    parser.add_option("-S", "--seed-source", dest="seeds")
    parser.add_option("-s", "--seed-dist", dest="release")
    parser.add_option("-C", "--apt-config", dest="apt_config")
    parser.add_option("-a", "--arch", dest="arch", default="amd64")
    parser.add_option(
        "--no-rdepends", dest="rdepends", action="store_false", default=True
    )
    options, _ = parser.parse_args(sys.argv[1:])

    if not os.path.exists(options.apt_config):
        sys.stderr.write("? no such apt config %s\n" % options.apt_config)
        sys.exit(1)

    # germinate logs to stdout; germinate-diff must not let that through.
    print("* Using seeds from %s arch=%s" % (options.seeds, options.arch))

    seed_order, inherit, branches = collect(
        options.seeds, options.release, set()
    )

    seeded = {
        name: read_seed(options.seeds, branches, name) for name in seed_order
    }

    # As in germinate, a package appears in only one seed's expanded list:
    # anything already provided by a seed this one inherits from is left out.
    expanded = {}
    ancestors = {}
    for name in seed_order:
        ancestors[name] = set()
        for parent in inherit[name]:
            ancestors[name].add(parent)
            ancestors[name] |= ancestors.get(parent, set())

        packages = set()
        for entry in seeded[name]:
            pkg, _, dep = entry.partition("+")
            packages.add(pkg)
            if dep:
                packages.add(dep)
        for ancestor in ancestors[name]:
            packages -= expanded.get(ancestor, set())
        expanded[name] = packages

    with open("structure", "w", encoding="UTF-8") as f:
        for name in seed_order:
            print("%s: %s" % (name, " ".join(inherit[name])), file=f)

    everything = set()
    for name in seed_order:
        everything |= expanded[name]
        with open("%s.json" % name, "w", encoding="UTF-8") as f:
            json.dump(sorted(expanded[name]), f)

    with open("extra.json", "w", encoding="UTF-8") as f:
        json.dump([], f)
    with open("all.json", "w", encoding="UTF-8") as f:
        json.dump(sorted(everything), f)

    return 0


if __name__ == "__main__":
    sys.exit(main())

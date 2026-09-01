#! /usr/bin/env python3
"""A stand-in for germinate, for testing germidiff without an archive.

It accepts the arguments germidiff passes, resolves seed branches the way
germinate does (``<seed-source>/<branch>/STRUCTURE``, following ``include``
lines), and writes the output files germidiff reads back: ``structure``
and one ``<seed>.json`` per seed.

"Expanding" a seed here means its own packages, plus a fake dependency for
any package written as ``name+dep`` (a hard dependency) or ``name~dep`` (a
Recommends) in a seed file, minus anything already provided by a seed it
inherits from -- the same one-list-per-package rule germinate follows.  That
is enough to exercise everything on the germidiff side without needing
apt.

Alongside the JSON, it writes the table-formatted ``<seed>`` and
``<seed>.seed`` files in germinate's own layout, since the retention check
reads the ``Why`` column out of them.
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


def _split_entry(entry):
    """Split "pkg", "pkg+dep" or "pkg~dep" into (pkg, kind, dep)."""
    for kind in ("+", "~"):
        if kind in entry:
            pkg, _, dep = entry.partition(kind)
            return pkg, kind, dep
    return entry, None, None


_COLUMNS = ("Package", "Source", "Why", "Maintainer", "Deb Size (B)",
            "Inst Size (KB)")


def _write_table(filename, rows):
    """Write a list in germinate's table format, headers and rules included."""
    widths = [len(c) for c in _COLUMNS]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    with open(filename, "w", encoding="UTF-8") as f:
        print(" | ".join(c.ljust(w) for c, w in zip(_COLUMNS, widths)),
              file=f)
        print("-+-".join("-" * w for w in widths), file=f)
        for row in rows:
            print(" | ".join(c.ljust(w) for c, w in zip(row, widths)),
                  file=f)
        print("-" * (sum(widths) + 3 * (len(widths) - 1)), file=f)
        print(" " * widths[0] + " | " + "0", file=f)


def _write_outputs(options, seed_order, inherit, seeded, expanded, why):
    """Write the human-readable lists the retention check reads."""
    for name in seed_order:
        explicit = set()
        for entry in seeded[name]:
            pkg, _, _ = _split_entry(entry)
            explicit.add(pkg)

        def rows(packages):
            return [
                (pkg, pkg, why.get((name, pkg), ""), "Nobody <n@example.org>",
                 "0", "0")
                for pkg in sorted(packages)
            ]

        _write_table(name, rows(expanded[name]))
        _write_table(name + ".seed", rows(explicit & expanded[name]))
        _write_table(name + ".seed-recommends", [])


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

    # germinate logs to stdout; germidiff must not let that through.
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
    why = {}
    for name in seed_order:
        ancestors[name] = set()
        for parent in inherit[name]:
            ancestors[name].add(parent)
            ancestors[name] |= ancestors.get(parent, set())

        packages = set()
        for entry in seeded[name]:
            pkg, kind, dep = _split_entry(entry)
            packages.add(pkg)
            why.setdefault(
                (name, pkg),
                "%s %s seed" % (options.release.title(), name),
            )
            if dep:
                packages.add(dep)
                why.setdefault(
                    (name, dep),
                    "%s (Recommends)" % pkg if kind == "~" else pkg,
                )
        for ancestor in ancestors[name]:
            packages -= expanded.get(ancestor, set())
        expanded[name] = packages

    _write_outputs(options, seed_order, inherit, seeded, expanded, why)

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

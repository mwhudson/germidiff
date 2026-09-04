"""Running germinate against a seed checkout and reading back its output."""

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

import json
import logging
import os
import subprocess

from germidiff.chdist import chdist_base
from germidiff.collection_map import validate_entry
from germidiff.structure import (
    StructureError,
    inherited_seeds,
    parse_inheritance,
    required_branches,
    seed_names_from_structure_output,
)

__all__ = [
    "GerminateError",
    "GerminateRun",
    "apt_config_for_chdist",
    "arch_for_apt_config",
    "architectures_for_apt_config",
    "build_seed_base",
    "components_for_apt_config",
    "germinate_command",
    "read_run_output",
    "resolve_dependencies",
    "run_germinate",
]

_logger = logging.getLogger("germidiff")

# Germinate always writes its output files into the current directory, so each
# run needs a directory of its own.  These are the two it writes that we care
# about: the merged structure (which tells us the seed names) and one JSON
# file per seed holding that seed's expanded package list.
STRUCTURE_OUTPUT = "structure"

# germinate writes <seedname>.json for every seed plus "extra", and all.json
# for the whole collection.  "extra" is not a real seed -- it is germinate's
# residue of packages built by seeded sources but not in any seed -- and
# all.json includes build-dependencies, so neither is part of the per-seed
# diff by default.
EXTRA_SEED = "extra"




class GerminateError(Exception):
    """Germinate could not be run, or did not produce usable output."""


def apt_config_for_chdist(chdist, base=None):
    """Work out the ``APT_CONFIG`` file for a chdist.

    ``chdist`` is normally the name of a chdist, resolved under
    ``base`` -- germidiff's own chdist directory, not the one the ``chdist``
    tool keeps for you.  For convenience it may also be a path to a chdist
    directory or straight to an ``apt.conf``, which is how a chdist of your
    own is named without moving the whole search.
    """
    expanded = os.path.expanduser(chdist)
    if os.path.isfile(expanded):
        return os.path.abspath(expanded)
    if os.path.isdir(expanded):
        candidate = os.path.join(expanded, "etc", "apt", "apt.conf")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    base = chdist_base(base)

    directory = os.path.join(base, chdist)
    candidate = os.path.join(directory, "etc", "apt", "apt.conf")
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)

    if not os.path.isdir(directory):
        raise GerminateError(
            "no chdist named %r under %s (germidiff keeps its own chdists "
            "there; for one of yours pass its path, or --chdist-base %s)"
            % (
                chdist,
                base,
                os.path.join(os.path.expanduser("~"), ".chdist"),
            )
        )
    raise GerminateError(
        "chdist %s has no etc/apt/apt.conf (looked in %s)"
        % (chdist, directory)
    )


def _apt_config_values(apt_config, key):
    """Ask apt for a configuration key, as a list of non-empty values."""
    if not os.path.isfile(apt_config):
        # apt quietly falls back to the host's own configuration when
        # APT_CONFIG points at nothing, which would answer about the host
        # rather than the chdist.
        return None
    env = dict(os.environ, APT_CONFIG=apt_config)
    try:
        proc = subprocess.run(
            ["apt-config", "dump", "--format", "%v%n", key],
            env=env,
            capture_output=True,
            encoding="UTF-8",
            errors="replace",
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def arch_for_apt_config(apt_config):
    """Return the architecture an apt config is set up for, or ``None``.

    A chdist is created for one architecture, and germinate has to be told
    the same one: pointing it at an amd64 chdist while asking for arm64
    silently produces a germination against the wrong Packages files rather
    than an error.  ``chdist create`` writes ``APT::Architecture`` into the
    config, so ask apt for it rather than making the user repeat it.
    """
    values = _apt_config_values(apt_config, "APT::Architecture")
    return values[0] if values else None


def architectures_for_apt_config(apt_config):
    """The architectures an apt config carries indexes for, or ``None``.

    Germinating for an architecture the chdist does not have is not an error
    and does not even look like one -- apt serves the Packages files it has,
    germinate resolves what it can, and the result is a plausible diff built
    from the wrong archive.  On a real collection it is genuinely
    indistinguishable from a good run by any measure taken after the fact, so
    it has to be caught here, before germinating.
    """
    values = _apt_config_values(apt_config, "APT::Architectures")
    if not values:
        arch = arch_for_apt_config(apt_config)
        return [arch] if arch else None
    return values


def components_for_apt_config(apt_config):
    """The archive components an apt config exposes, or ``None``.

    Which components are in play is a property of the chdist, not of
    germidiff: germinate ignores ``--components`` when given
    ``--apt-config``, and reads whatever indexes apt has.  That is the right
    behaviour but an easy thing to get wrong, since the platform and ubuntu
    collections are germinated against main and restricted while flavours use
    every component, so it is worth being able to say which was used.  Asked
    of apt the same way :class:`germinate.archive.AptArchive` asks.
    """
    if not os.path.isfile(apt_config):
        return None
    env = dict(os.environ, APT_CONFIG=apt_config)
    try:
        proc = subprocess.run(
            [
                "apt-get",
                "indextargets",
                "--format",
                "$(COMPONENT)",
                "Identifier: Packages",
            ],
            env=env,
            capture_output=True,
            encoding="UTF-8",
            errors="replace",
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    seen = []
    for line in proc.stdout.splitlines():
        component = line.strip()
        if component and component not in seen:
            seen.append(component)
    return seen or None


def build_seed_base(base_dir, under_test_branch, under_test_dir, branch_dirs):
    """Lay out a seed source directory germinate can resolve branches in.

    Germinate looks for a branch's seeds at ``<seed-base>/<branch>/``, both
    for the branch named by ``--seed-dist`` and for any branch named by an
    ``include`` line, so a directory of symlinks is all it takes to point it
    at local checkouts.  The collection under test always wins over an entry
    of the same name in the collection map.

    Only whole collections get a symlink.  A branch name may be a path --
    ``include ubuntu.stonking/languages`` names a collection nested inside
    ``ubuntu.stonking`` -- and those are reached through their parent's
    symlink.  Linking them separately would mean writing inside the parent's
    checkout, since the path leading to them runs through that symlink.
    """
    os.makedirs(base_dir, exist_ok=True)
    links = dict(branch_dirs)
    links[under_test_branch] = under_test_dir
    for branch, directory in sorted(links.items()):
        if "/" in branch:
            raise GerminateError(
                "cannot place nested collection %s in a seed source "
                "directory; it is reached through %s"
                % (branch, branch.split("/", 1)[0])
            )
        os.symlink(
            os.path.abspath(directory), os.path.join(base_dir, branch)
        )
    return base_dir


def resolve_dependencies(
    under_test_branch, under_test_dir, collection_map, neighbours=None
):
    """Find the local directory for every branch the collection needs.

    Returns a ``(directories, links)`` pair.  ``directories`` maps every
    branch the collection pulls in through ``include`` lines to its local
    directory; ``links`` is the subset that needs a symlink of its own in the
    seed source directory, which is to say the whole collections rather than
    those nested inside one.  Raises GerminateError naming what is missing.

    A branch is looked for, in order, in the collection map, inside a
    collection already resolved (for a nested name like
    ``ubuntu.stonking/languages``), and then among ``neighbours`` -- the
    directories beside the repo under test, which is where a seed branch
    usually has its siblings checked out.
    """
    resolved = {under_test_branch: under_test_dir}
    nested = set()

    def resolve(branch):
        if branch in resolved:
            return resolved[branch]

        directory = collection_map.get(branch)
        if directory is not None:
            # Checked here rather than at load time, so that a map listing
            # collections this run does not need never gets in the way.
            validate_entry(branch, directory)
            if "/" in branch:
                raise GerminateError(
                    "collection %s is nested inside %s and is read from "
                    "there; it cannot be mapped to %s"
                    % (branch, branch.split("/", 1)[0], directory)
                )
            resolved[branch] = directory
            return directory

        # "include ubuntu.stonking/languages" names a collection inside
        # ubuntu.stonking, so it comes with its parent and needs nothing
        # said about it.
        if "/" in branch:
            head, tail = branch.split("/", 1)
            parent = resolve(head)
            if parent is not None:
                directory = os.path.join(parent, tail)
                if os.path.isfile(os.path.join(directory, "STRUCTURE")):
                    resolved[branch] = directory
                    nested.add(branch)
                    return directory
            return None

        for neighbour in neighbours or ():
            directory = os.path.join(neighbour, branch)
            if os.path.isfile(os.path.join(directory, "STRUCTURE")):
                resolved[branch] = directory
                return directory
        return None

    try:
        order, missing = required_branches(under_test_branch, resolve)
    except StructureError as e:
        raise GerminateError(str(e))

    if missing:
        lines = [
            "no local checkout for %d seed collection(s) needed by %s:"
            % (len(missing), under_test_branch)
        ]
        for branch, included_by in missing:
            lines.append("  %s (included by %s)" % (branch, included_by))
        if neighbours:
            lines.append(
                "Checked out beside the seed repo (%s) or named with "
                "--collection." % ", ".join(neighbours)
            )
        else:
            lines.append(
                "Add them to the collection map (see --collection-map and "
                "--collection)."
            )
        raise GerminateError("\n".join(lines))

    directories = {branch: resolved[branch] for branch in order}
    links = {
        branch: directory
        for branch, directory in directories.items()
        if branch not in nested
    }
    return directories, links


class GerminateRun:
    """The result of one germinate run: expanded package lists per seed."""

    def __init__(self, label, ref, seed_names, seeds, inherit=None):
        self.label = label
        self.ref = ref
        #: Seed names in germinate's inheritance order.
        self.seed_names = seed_names
        #: Seed name to the set of packages in its expanded list.  Germinate
        #: lists a package only in the seed that first pulls it in, so this
        #: is what a seed newly accounts for, not what it contains.
        self.seeds = seeds
        #: Seed name to every seed it inherits from, nearest last.
        self.inherit = inherit or {}

    def inclusive(self, name):
        """Everything a seed contains, its inherited seeds included.

        This is what an image built from the seed would have, as against
        :attr:`seeds`, which holds only what the seed itself accounts for.
        """
        packages = set(self.seeds.get(name, ()))
        for inherited in self.inherit.get(name, ()):
            packages |= self.seeds.get(inherited, set())
        return packages

    def union(self, exclude_extra=False):
        """The union of every seed's expanded package list.

        ``exclude_extra`` drops germinate's ``extra`` pseudo-seed, so that
        the result is comparable with a germination that only looked at the
        real seeds regardless of whether ``--include-extra`` was given.
        """
        result = set()
        for name, packages in self.seeds.items():
            if exclude_extra and name == EXTRA_SEED:
                continue
            result |= packages
        return result


def _read_seed_json(out_dir, seedname):
    path = os.path.join(out_dir, "%s.json" % seedname)
    try:
        with open(path, encoding="UTF-8") as f:
            packages = json.load(f)
    except OSError as e:
        raise GerminateError(
            "germinate produced no expanded list for seed %s: %s"
            % (seedname, e)
        )
    except ValueError as e:
        raise GerminateError("could not parse %s: %s" % (path, e))
    if not isinstance(packages, list):
        raise GerminateError(
            "%s: expected a JSON list of package names" % path
        )
    return set(packages)


def read_run_output(out_dir, label, ref, include_extra=False):
    """Read the per-seed expanded package lists from a germinate run."""
    structure_path = os.path.join(out_dir, STRUCTURE_OUTPUT)
    if not os.path.isfile(structure_path):
        raise GerminateError(
            "germinate wrote no %s file in %s; it probably failed"
            % (STRUCTURE_OUTPUT, out_dir)
        )
    try:
        seed_names = seed_names_from_structure_output(structure_path)
    except StructureError as e:
        raise GerminateError(str(e))

    if include_extra and os.path.isfile(
        os.path.join(out_dir, "%s.json" % EXTRA_SEED)
    ):
        seed_names = seed_names + [EXTRA_SEED]

    seeds = {}
    for seedname in seed_names:
        seeds[seedname] = _read_seed_json(out_dir, seedname)
    inherit = inherited_seeds(parse_inheritance(structure_path))
    return GerminateRun(label, ref, seed_names, seeds, inherit=inherit)


def germinate_command(
    germinate, seed_base, seed_dist, apt_config, arch, extra_args=()
):
    """Build the germinate command line for one run.

    ``--seed-source`` is the symlink farm built by :func:`build_seed_base` and
    ``--seed-dist`` the branch under test within it; with no ``--vcs`` option
    germinate treats the source as a plain directory and resolves branches
    (including those named by ``include`` lines) beneath it.  ``--apt-config``
    points at the chdist, and is the same for both runs so that the diff
    reflects only the seed change.
    """
    if os.sep in germinate:
        # Germinate runs with its output directory as the working directory,
        # so a relative path to it has to be resolved before we get there.
        germinate = os.path.abspath(os.path.expanduser(germinate))
    command = [
        germinate,
        "--seed-source",
        seed_base,
        "--seed-dist",
        seed_dist,
        "--apt-config",
        apt_config,
        "--arch",
        arch,
    ]
    command.extend(extra_args)
    return command


def run_germinate(
    germinate,
    out_dir,
    seed_base,
    seed_dist,
    apt_config,
    arch,
    extra_args=(),
    log_path=None,
):
    """Run germinate in ``out_dir``, which it fills with its output files.

    Germinate logs to stdout, so its output is captured rather than let
    through: our own stdout is reserved for the diff.  ``log_path``, if
    given, is where that output is saved for later inspection.
    """
    os.makedirs(out_dir, exist_ok=True)
    command = germinate_command(
        germinate, seed_base, seed_dist, apt_config, arch, extra_args
    )

    _logger.info("running: %s (in %s)", " ".join(command), out_dir)
    try:
        proc = subprocess.run(
            command,
            cwd=out_dir,
            capture_output=True,
            encoding="UTF-8",
            errors="replace",
        )
    except OSError as e:
        raise GerminateError("could not run %s: %s" % (germinate, e))

    output = proc.stdout + proc.stderr
    if log_path is not None:
        try:
            with open(log_path, "w", encoding="UTF-8") as f:
                f.write(output)
        except OSError as e:  # pragma: no cover - diagnostics only
            _logger.warning("could not write %s: %s", log_path, e)

    if proc.returncode != 0:
        raise GerminateError(
            "germinate failed with exit status %d:\n%s"
            % (proc.returncode, output.strip())
        )

    # bin/germinate discards main()'s return value, so a clean exit status is
    # not by itself proof that the run worked; the caller checks the output
    # files.  Keep the log around so a failure there can be explained.
    return output

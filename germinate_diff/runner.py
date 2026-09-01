"""Running germinate against a seed checkout and reading back its output."""

import json
import logging
import os
import subprocess

from germinate_diff.collection_map import validate_entry
from germinate_diff.structure import (
    StructureError,
    required_branches,
    seed_names_from_structure_output,
)

__all__ = [
    "GerminateError",
    "GerminateRun",
    "apt_config_for_chdist",
    "arch_for_apt_config",
    "build_seed_base",
    "germinate_command",
    "read_run_output",
    "resolve_dependencies",
    "run_germinate",
]

_logger = logging.getLogger("germinate-diff")

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

# How many unresolvable packages germinate may report before we say so.
# A real germination of an Ubuntu collection reports some; hundreds mean
# something is wrong with the inputs.
PROBLEM_WARNING_THRESHOLD = 100


class GerminateError(Exception):
    """Germinate could not be run, or did not produce usable output."""


def apt_config_for_chdist(chdist, chdist_base=None):
    """Work out the ``APT_CONFIG`` file for a chdist.

    ``chdist`` is normally the name of a chdist, resolved under
    ``chdist_base`` (``$CHDIST_HOME``, or ``~/.chdist``) the same way the
    ``chdist`` tool itself resolves it.  For convenience it may also be a path
    to a chdist directory or straight to an ``apt.conf``.
    """
    expanded = os.path.expanduser(chdist)
    if os.path.isfile(expanded):
        return os.path.abspath(expanded)
    if os.path.isdir(expanded):
        candidate = os.path.join(expanded, "etc", "apt", "apt.conf")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    if chdist_base is None:
        chdist_base = os.environ.get("CHDIST_HOME")
    if not chdist_base:
        chdist_base = os.path.join(os.path.expanduser("~"), ".chdist")
    chdist_base = os.path.expanduser(chdist_base)

    directory = os.path.join(chdist_base, chdist)
    candidate = os.path.join(directory, "etc", "apt", "apt.conf")
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)

    if not os.path.isdir(directory):
        raise GerminateError(
            "no chdist named %r under %s (create one with "
            "'chdist create %s')" % (chdist, chdist_base, chdist)
        )
    raise GerminateError(
        "chdist %s has no etc/apt/apt.conf (looked in %s)"
        % (chdist, directory)
    )


def arch_for_apt_config(apt_config):
    """Return the architecture an apt config is set up for, or ``None``.

    A chdist is created for one architecture, and germinate has to be told
    the same one: pointing it at an amd64 chdist while asking for arm64
    silently produces a germination against the wrong Packages files rather
    than an error.  ``chdist create`` writes ``APT::Architecture`` into the
    config, so ask apt for it rather than making the user repeat it.
    """
    if not os.path.isfile(apt_config):
        # apt quietly falls back to the host's own configuration when
        # APT_CONFIG points at nothing, which would answer with the host's
        # architecture rather than the chdist's.
        return None
    env = dict(os.environ, APT_CONFIG=apt_config)
    try:
        proc = subprocess.run(
            ["apt-config", "dump", "--format", "%v%n", "APT::Architecture"],
            env=env,
            capture_output=True,
            encoding="UTF-8",
            errors="replace",
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    arch = proc.stdout.strip()
    return arch or None


def build_seed_base(base_dir, under_test_branch, under_test_dir, branch_dirs):
    """Lay out a seed source directory germinate can resolve branches in.

    Germinate looks for a branch's seeds at ``<seed-base>/<branch>/``, both
    for the branch named by ``--seed-dist`` and for any branch named by an
    ``include`` line, so a directory of symlinks is all it takes to point it
    at local checkouts.  The collection under test always wins over an entry
    of the same name in the collection map.
    """
    os.makedirs(base_dir, exist_ok=True)
    os.symlink(
        os.path.abspath(under_test_dir),
        os.path.join(base_dir, under_test_branch),
    )
    for branch, directory in sorted(branch_dirs.items()):
        if branch == under_test_branch:
            continue
        os.symlink(
            os.path.abspath(directory), os.path.join(base_dir, branch)
        )
    return base_dir


def resolve_dependencies(under_test_branch, under_test_dir, collection_map):
    """Find the local directory for every branch the collection needs.

    Returns a dict of branch name to directory, covering the collection under
    test and everything it pulls in through ``include`` lines.  Raises
    GerminateError naming what to add to the collection map if anything is
    missing.
    """

    def resolve(branch):
        if branch == under_test_branch:
            return under_test_dir
        directory = collection_map.get(branch)
        if directory is not None:
            # Checked here rather than at load time, so that a map listing
            # collections this run does not need never gets in the way.
            validate_entry(branch, directory)
        return directory

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
        lines.append(
            "Add them to the collection map (see --collection-map and "
            "--collection)."
        )
        raise GerminateError("\n".join(lines))

    return {branch: resolve(branch) for branch in order}


class GerminateRun:
    """The result of one germinate run: expanded package lists per seed."""

    def __init__(self, label, ref, seed_names, seeds):
        self.label = label
        self.ref = ref
        #: Seed names in germinate's inheritance order.
        self.seed_names = seed_names
        #: Seed name to the set of packages in its expanded list.
        self.seeds = seeds

    def union(self):
        """The union of every seed's expanded package list."""
        result = set()
        for packages in self.seeds.values():
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
    return GerminateRun(label, ref, seed_names, seeds)


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

    # Germinate prefixes lines about packages it could not resolve with "?".
    # A handful is normal; a flood usually means it was pointed at the wrong
    # archive -- most often an --arch the chdist does not carry -- which
    # otherwise shows up as a plausible-looking but meaningless diff.
    problems = sum(1 for line in output.splitlines() if line.startswith("?"))
    if problems > PROBLEM_WARNING_THRESHOLD:
        where = (
            "see %s" % log_path
            if log_path
            else "re-run with --keep to see germinate's own output"
        )
        _logger.warning(
            "germinate could not resolve %d packages or dependencies; "
            "check that --arch %s matches the chdist (%s)",
            problems,
            arch,
            where,
        )

    # bin/germinate discards main()'s return value, so a clean exit status is
    # not by itself proof that the run worked; the caller checks the output
    # files.  Keep the log around so a failure there can be explained.
    return output

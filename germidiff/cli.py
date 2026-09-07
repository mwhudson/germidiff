"""Command-line entry point for germidiff."""

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

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile

from germidiff import VERSION
from germidiff.chdist import (
    ChdistError,
    DEFAULT_MIRROR,
    chdist_base,
    chdist_location,
    components_for_collection,
    ensure_chdist,
    update_chdist,
)
from germidiff.diff import diff_runs
from germidiff.metapackage import pending_edges
from germidiff.probe import ProbeError, probe_cuts
from germidiff.report import format_diff
from germidiff.retention import (
    find_retained,
    soft_edges,
    without_edges,
)
from germidiff.runner import (
    GerminateError,
    apt_config_for_chdist,
    arch_for_apt_config,
    architectures_for_apt_config,
    build_seed_base,
    components_for_apt_config,
    read_run_output,
    resolve_dependencies,
    run_germinate,
)
from germidiff.seedtree import split_branch
from germidiff.structure import StructureError
from germidiff.worktree import (
    GitError,
    check_repo,
    describe_head,
    resolve_ref,
    worktrees,
)

_logger = logging.getLogger("germidiff")

# Only used when the chdist does not say what it was created for, which
# should not happen for one made by 'chdist create'.
DEFAULT_ARCH = "amd64"


def host_arch():
    """The architecture to create a chdist for when nothing says otherwise.

    Only needed for a chdist that does not exist yet: an existing one is
    asked what it was made for.
    """
    try:
        proc = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True,
            encoding="UTF-8",
            errors="replace",
        )
    except OSError:
        return DEFAULT_ARCH
    if proc.returncode != 0:
        return DEFAULT_ARCH
    return proc.stdout.strip() or DEFAULT_ARCH


DESCRIPTION = """\
Show the consequences of a proposed change to an Ubuntu seed collection.

Runs germinate twice against the same archive metadata -- once with the seed
collection at OLD-REF and once at NEW-REF -- and diffs the resulting expanded
per-seed package lists.  Any other seed collection the one under test depends
on is held fixed at the checkout beside it, so the diff reflects only the seed
change.

With no CHDIST, the archive to read is worked out from the collection's own
branch name: ubuntu.resolute is the resolute archive, and the ubuntu
collection germinates against main and restricted.  A chdist for that is
created if it is not there already, and refreshed before the runs.
"""

EPILOG = """\
A collection the one under test includes -- in practice the platform
collection -- is read from the directory of that name beside SEED-REPO, which
is where seed branches are normally checked out.  germidiff-mp clones them
there for you; locally they are expected to be in place already.

Progress goes to standard error and the report to standard output, so the
report can be redirected on its own.

Exits 0 when both germinate runs succeeded, whether or not there were any
differences, and nonzero if anything went wrong.
"""


def log_level(args):
    """How much to say on stderr.

    Progress is on by default: a run germinates twice over a real archive
    and can sit for a minute with nothing to show, and the report goes to
    stdout, so saying what is happening costs the output nothing.
    """
    if args.quiet:
        return logging.WARNING
    return logging.DEBUG if args.verbose else logging.INFO


def configure_logging(args, prefix):
    """Send progress to standard error, at the level the options ask for.

    Set up on germidiff's own logger rather than through basicConfig, which
    configures the root logger once per process and then quietly does
    nothing -- leaving anything that runs a second command in the same
    process writing to the first one's stderr.
    """
    logger = logging.getLogger("germidiff")
    for handler in list(logger.handlers):
        # Only ours: a caller's handlers, and the one unittest attaches to
        # watch for warnings, are none of our business.
        if getattr(handler, "germidiff_progress", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%s: %%(message)s" % prefix))
    handler.germidiff_progress = True
    logger.addHandler(handler)
    logger.setLevel(log_level(args))


def add_analysis_options(parser):
    """Add the options that shape the analysis rather than choose its inputs.

    Shared with germidiff-mp, which works out what to germinate from a merge
    proposal but offers the same control over what is done with it.
    """
    parser.add_argument(
        "-a",
        "--arch",
        help="architecture to germinate for (default: the architecture the "
        "chdist was created for)",
    )
    parser.add_argument(
        "--germinate",
        default="germinate",
        metavar="PATH",
        help="germinate executable to run (default: %(default)s)",
    )
    parser.add_argument(
        "--germinate-arg",
        metavar="ARG",
        action="append",
        default=[],
        help="pass an extra argument to germinate; repeatable.  Use the "
        "--germinate-arg=ARG form for arguments starting with a dash, which "
        "are otherwise read as germidiff's own options",
    )
    parser.add_argument(
        "--rdepends",
        action="store_true",
        help="let germinate compute reverse dependencies; off by default "
        "because it writes a file per package and does not affect the "
        "expanded lists we diff",
    )
    parser.add_argument(
        "--no-metapackage-probe",
        dest="metapackage_probe",
        action="store_false",
        default=True,
        help="do not germinate again to show what happens once metapackages "
        "generated from these seeds are rebuilt; that probe runs by default, "
        "but only when there is something for it to say",
    )
    parser.add_argument(
        "--probe-retention",
        action="store_true",
        help="for each package now held only by a Recommends, germinate "
        "again with that one dependency cut and report what actually falls "
        "out; costs a germination per package, so it is off by default",
    )
    parser.add_argument(
        "--whole-seed-lists",
        action="store_true",
        help="list every package of a seed the change added or removed, "
        "instead of summarising the ones that only moved between seeds",
    )
    parser.add_argument(
        "--include-extra",
        action="store_true",
        help="also diff germinate's 'extra' pseudo-seed (packages built by "
        "seeded sources but in no seed)",
    )
    parser.add_argument(
        "--work-dir",
        metavar="DIR",
        help="use DIR for worktrees and germinate output instead of a "
        "temporary directory; implies --keep",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the worktrees and germinate output for inspection",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also report every command run, not just what is going on",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="report only problems, not progress",
    )


def add_chdist_options(parser):
    """Add the options that shape the chdist the archive is read through.

    Shared with germidiff-mp: both commands work out a chdist from the
    collection and series unless they are handed one, and both have to be
    able to say what the archive should look like when they do.
    """
    parser.add_argument(
        "--chdist-base",
        metavar="DIR",
        help="directory germidiff keeps its chdists in (default: "
        "%s); pass ~/.chdist to use the ones you made yourself" % (
            chdist_base(),
        ),
    )
    parser.add_argument(
        "--mirror",
        metavar="URL",
        default=DEFAULT_MIRROR,
        help="archive to create a missing chdist against (default: "
        "%(default)s)",
    )
    parser.add_argument(
        "--components",
        metavar="LIST",
        help="components to germinate against, space or comma separated, "
        "overriding what the collection implies",
    )
    parser.add_argument(
        "--no-check-components",
        dest="check_components",
        action="store_false",
        default=True,
        help="use an existing chdist even if it does not offer exactly the "
        "components this collection germinates against",
    )
    parser.add_argument(
        "--no-update",
        dest="update",
        action="store_false",
        default=True,
        help="germinate against the apt lists as they stand instead of "
        "refreshing them first; a chdist that had to be created is updated "
        "regardless, having no lists at all",
    )


def components_for(args, collection):
    """The components a collection germinates against, --components winning."""
    if args.components:
        return tuple(args.components.replace(",", " ").split())
    return components_for_collection(collection)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="germidiff",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + VERSION
    )
    parser.add_argument(
        "seed_repo",
        metavar="SEED-REPO",
        help="git repo of the seed collection under test",
    )
    parser.add_argument(
        "old_ref", metavar="OLD-REF", help="git ref to diff from"
    )
    parser.add_argument(
        "new_ref", metavar="NEW-REF", help="git ref to diff to"
    )
    parser.add_argument(
        "chdist",
        metavar="CHDIST",
        nargs="?",
        help="chdist providing the archive metadata for both runs: the name "
        "of one under --chdist-base, or the path to any chdist directory or "
        "apt.conf (default: the one the collection's branch name implies, "
        "created if it is not there already)",
    )

    parser.add_argument(
        "-s",
        "--seed-dist",
        metavar="BRANCH",
        help="branch name of the collection under test, as it would appear "
        "in an 'include' line (default: the repo's directory name)",
    )
    add_chdist_options(parser)
    add_analysis_options(parser)

    args = parser.parse_args(argv)
    if args.work_dir:
        args.keep = True
    return args


def _germinate_args(args):
    extra = []
    if not args.rdepends:
        extra.append("--no-rdepends")
    extra.extend(args.germinate_arg)
    return extra


def _make_work_dir(args):
    if args.work_dir:
        os.makedirs(args.work_dir, exist_ok=True)
        return os.path.abspath(args.work_dir), False
    return tempfile.mkdtemp(prefix="germidiff-"), not args.keep


def resolve_chdist(args, seed_dist):
    """The chdist both runs read the archive through.

    Given one, take it as it stands and refresh it; given none, work it out
    from the branch name the way germidiff-mp works it out from a merge
    proposal -- ubuntu.resolute is the ubuntu collection of the resolute
    series -- and create it if it is not there.
    """
    if args.chdist is not None:
        # Resolved before being refreshed, so that a name that means nothing
        # is reported as such rather than by chdist failing to update it.
        apt_config = apt_config_for_chdist(args.chdist, args.chdist_base)
        if args.update:
            base, name = chdist_location(args.chdist, args.chdist_base)
            update_chdist(name, base)
        return args.chdist, apt_config

    collection, series = split_branch(seed_dist)
    if series is None:
        raise GerminateError(
            "cannot tell which archive %s means: a branch name is read as "
            "collection.series, as germinate reads it, so %s says nothing "
            "about a series.  Name a chdist, or give the branch with "
            "--seed-dist" % (seed_dist, seed_dist)
        )
    components = components_for(args, collection)
    _logger.info(
        "%s is the %s collection of %s, germinated against %s",
        seed_dist, collection, series, " ".join(components),
    )
    chdist = ensure_chdist(
        series,
        components,
        args.arch or host_arch(),
        base=args.chdist_base,
        mirror=args.mirror,
        update=args.update,
        check_components=args.check_components,
    )
    return chdist, apt_config_for_chdist(chdist, args.chdist_base)


def _one_side(
    args, label, ref, checkout, work_dir, apt_config, branch_dirs, seed_dist
):
    """Run germinate for one side of the diff and read its output back."""
    side_dir = os.path.join(work_dir, label)
    seed_base = build_seed_base(
        os.path.join(side_dir, "seeds"), seed_dist, checkout, branch_dirs
    )
    out_dir = os.path.join(side_dir, "out")
    log_path = os.path.join(side_dir, "germinate.log") if args.keep else None
    run_germinate(
        args.germinate,
        out_dir,
        seed_base,
        seed_dist,
        apt_config,
        args.arch,
        extra_args=_germinate_args(args),
        # Only worth writing where it will outlive the run: a failing run
        # reports germinate's output inline anyway.
        log_path=log_path,
    )
    run = read_run_output(
        out_dir, label, ref, include_extra=args.include_extra
    )
    return run, out_dir, seed_base


def run(args):
    """Do the work.  Returns the text to print on stdout."""
    repo = os.path.abspath(os.path.expanduser(args.seed_repo))
    check_repo(repo)
    old_commit = resolve_ref(repo, args.old_ref)
    new_commit = resolve_ref(repo, args.new_ref)

    seed_dist = args.seed_dist
    if seed_dist is None:
        seed_dist = os.path.basename(repo.rstrip(os.sep))
    _logger.info("collection under test: %s (%s)", seed_dist, repo)

    chdist, apt_config = resolve_chdist(args, seed_dist)
    if args.arch is None:
        args.arch = arch_for_apt_config(apt_config) or DEFAULT_ARCH
    else:
        available = architectures_for_apt_config(apt_config)
        if available and args.arch not in available:
            _logger.warning(
                "chdist %s carries %s, not %s; germinating for an "
                "architecture it does not have still succeeds, but the "
                "result comes from the wrong Packages files",
                chdist,
                "/".join(available),
                args.arch,
            )
    components = components_for_apt_config(apt_config)
    _logger.info(
        "archive metadata: %s (%s, %s)",
        apt_config,
        args.arch,
        " ".join(components) if components else "components unknown",
    )

    if old_commit == new_commit:
        _logger.warning(
            "%s and %s are the same commit (%s)",
            args.old_ref,
            args.new_ref,
            old_commit[:12],
        )

    work_dir, remove_work_dir = _make_work_dir(args)
    try:
        checkouts = [
            (os.path.join(work_dir, "old", "checkout"), old_commit),
            (os.path.join(work_dir, "new", "checkout"), new_commit),
        ]
        with worktrees(repo, checkouts, keep=args.keep) as (old_co, new_co):
            # The two sides must agree on which collections they depend on,
            # and the change under test may add or drop an include line, so
            # take the union of what each side needs.
            # Seed collections are normally checked out beside each
            # other, named for their branch, which is the same layout
            # germinate resolves a seed source against -- and the one
            # germidiff-mp clones into its cache.
            neighbours = [os.path.dirname(repo)]
            branch_dirs = {}
            nested = set()
            for checkout in (old_co, new_co):
                directories, links = resolve_dependencies(
                    seed_dist, checkout, neighbours
                )
                branch_dirs.update(links)
                nested.update(set(directories) - set(links))
            for branch in sorted(nested):
                _logger.info(
                    "%s is nested inside %s and comes with it",
                    branch,
                    branch.split("/", 1)[0],
                )
            # The collection under test is not a fixed dependency; each side
            # supplies its own worktree for it.
            branch_dirs.pop(seed_dist, None)
            for branch, directory in sorted(branch_dirs.items()):
                _logger.info(
                    "holding %s fixed at %s (%s)",
                    branch,
                    directory,
                    describe_head(directory),
                )

            old_run, old_out, _ = _one_side(
                args,
                "old",
                old_commit,
                old_co,
                work_dir,
                apt_config,
                branch_dirs,
                seed_dist,
            )
            new_run, new_out, new_seed_base = _one_side(
                args,
                "new",
                new_commit,
                new_co,
                work_dir,
                apt_config,
                branch_dirs,
                seed_dist,
            )

            # A change can leave every expanded list alone and still make a
            # package's presence rest on something nobody promised to keep,
            # so this runs whether or not anything differed.
            # A dependency from a metapackage this collection generates
            # is only as old as the last ubuntu-meta upload, so it can hide
            # the change completely until those are rebuilt.  Where that is
            # happening, germinate the new side again without those
            # dependencies and diff against that instead: one diff that says
            # what the change does, rather than one that says what it does
            # not do yet.
            retained = find_retained(old_out, new_out, new_run)
            metapackages = pending_edges(
                old_out, new_out, new_run.seed_names, retained
            )

            baseline = new_run.union(exclude_extra=True)
            if metapackages and args.metapackage_probe:
                try:
                    rebuilt = probe_cuts(
                        [
                            (edge.metapackage, edge.field, edge.package)
                            for edge in metapackages
                        ],
                        new_seed_base,
                        seed_dist,
                        apt_config,
                        args.arch,
                        baseline=baseline,
                    )
                except ProbeError as e:
                    _logger.warning(
                        "%s; reporting the archive as it stands instead", e
                    )
                    metapackages = []
                else:
                    if rebuilt.error is not None:
                        _logger.warning(
                            "could not germinate without those "
                            "dependencies: %s", rebuilt.error
                        )
                        metapackages = []
                    else:
                        new_run = rebuilt.after
                        retained = without_edges(
                            find_retained(old_out, new_out, new_run),
                            metapackages,
                        )
            elif metapackages:
                # Asked not to, so say what is being taken at face value.
                for edge in metapackages:
                    _logger.warning(
                        "%s is held by %s, which is built from the %s seed "
                        "and will drop it when rebuilt",
                        edge.package,
                        edge.metapackage,
                        edge.seed,
                    )
                metapackages = []

            probe = None
            if args.probe_retention:
                cuts = [
                    (holder, "Recommends", package)
                    for holder, package in soft_edges(retained)
                ]
                if not cuts:
                    _logger.info("nothing to probe: no soft retention found")
                else:
                    try:
                        probe = probe_cuts(
                            cuts,
                            new_seed_base,
                            seed_dist,
                            apt_config,
                            args.arch,
                            baseline=baseline,
                        )
                    except ProbeError as e:
                        _logger.warning("%s", e)
    finally:
        if remove_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        elif args.keep:
            _logger.info("left working files in %s", work_dir)

    return format_diff(
        diff_runs(old_run, new_run),
        retained=retained,
        probe=probe,
        metapackages=metapackages,
        whole_seed_lists=args.whole_seed_lists,
    )


def main(argv=None):
    args = parse_args(argv)
    configure_logging(args, "germidiff")
    try:
        text = run(args)
    except (
        ChdistError,
        GerminateError,
        GitError,
        StructureError,
    ) as e:
        print("germidiff: error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("germidiff: interrupted", file=sys.stderr)
        return 130

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

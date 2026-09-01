"""Command-line entry point for germinate-diff."""

import argparse
import logging
import os
import shutil
import sys
import tempfile

from germinate_diff import VERSION
from germinate_diff.collection_map import (
    CollectionMapError,
    DEFAULT_CONFIG_PATHS,
    load_collection_map,
)
from germinate_diff.diff import diff_runs
from germinate_diff.report import format_diff
from germinate_diff.runner import (
    GerminateError,
    apt_config_for_chdist,
    build_seed_base,
    read_run_output,
    resolve_dependencies,
    run_germinate,
)
from germinate_diff.structure import StructureError
from germinate_diff.worktree import (
    GitError,
    check_repo,
    describe_head,
    resolve_ref,
    worktrees,
)

_logger = logging.getLogger("germinate-diff")

DESCRIPTION = """\
Show the consequences of a proposed change to an Ubuntu seed collection.

Runs germinate twice against the same archive metadata -- once with the seed
collection at OLD-REF and once at NEW-REF -- and diffs the resulting expanded
per-seed package lists.  Any other seed collection the one under test depends
on is held fixed at a local checkout, so the diff reflects only the seed
change.
"""

EPILOG = """\
The collection map tells germinate-diff where the seed collections that this
one includes live locally.  It is an ini file with a [collections] section
mapping branch name to directory, for example:

  [collections]
  platform.questing = ~/seeds/platform

With no --collection-map, it is read from the first of these that exists:

%s

--collection-map replaces that search with a file of your choosing, and
--collection adds or overrides individual entries on top of whichever file
was used.

Exits 0 when both germinate runs succeeded, whether or not there were any
differences, and nonzero if anything went wrong.
""" % "\n".join(
    "  " + path for path in DEFAULT_CONFIG_PATHS
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="germinate-diff",
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
        help="chdist providing the archive metadata for both runs",
    )

    parser.add_argument(
        "-s",
        "--seed-dist",
        metavar="BRANCH",
        help="branch name of the collection under test, as it would appear "
        "in an 'include' line (default: looked up in the collection map by "
        "path, falling back to the repo's directory name)",
    )
    parser.add_argument(
        "--collection-map",
        metavar="FILE",
        help="ini file mapping seed collection branch names to local "
        "checkouts (default: the first of the paths listed below that "
        "exists)",
    )
    parser.add_argument(
        "--collection",
        metavar="BRANCH=DIR",
        action="append",
        default=[],
        help="add or override one collection map entry; repeatable",
    )
    parser.add_argument(
        "--chdist-base",
        metavar="DIR",
        help="directory holding chdists (default: $CHDIST_HOME, or "
        "~/.chdist)",
    )
    parser.add_argument(
        "-a",
        "--arch",
        default="amd64",
        help="architecture to germinate for (default: %(default)s)",
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
        help="pass an extra argument to germinate; repeatable",
    )
    parser.add_argument(
        "--rdepends",
        action="store_true",
        help="let germinate compute reverse dependencies; off by default "
        "because it writes a file per package and does not affect the "
        "expanded lists we diff",
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
        help="report progress on stderr",
    )

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
    return tempfile.mkdtemp(prefix="germinate-diff-"), not args.keep


def _one_side(
    args, label, ref, checkout, work_dir, apt_config, branch_dirs, seed_dist
):
    """Run germinate for one side of the diff and read its output back."""
    side_dir = os.path.join(work_dir, label)
    seed_base = build_seed_base(
        os.path.join(side_dir, "seeds"), seed_dist, checkout, branch_dirs
    )
    out_dir = os.path.join(side_dir, "out")
    run_germinate(
        args.germinate,
        out_dir,
        seed_base,
        seed_dist,
        apt_config,
        args.arch,
        extra_args=_germinate_args(args),
        log_path=os.path.join(side_dir, "germinate.log"),
    )
    return read_run_output(
        out_dir, label, ref, include_extra=args.include_extra
    )


def run(args):
    """Do the work.  Returns the text to print on stdout."""
    repo = os.path.abspath(os.path.expanduser(args.seed_repo))
    check_repo(repo)
    old_commit = resolve_ref(repo, args.old_ref)
    new_commit = resolve_ref(repo, args.new_ref)

    collection_map = load_collection_map(
        config_path=args.collection_map, overrides=args.collection
    )
    if collection_map.source:
        _logger.info("collection map: %s", collection_map.source)

    seed_dist = args.seed_dist
    if seed_dist is None:
        seed_dist = collection_map.branch_for_path(repo)
    if seed_dist is None:
        seed_dist = os.path.basename(repo.rstrip(os.sep))
    _logger.info("collection under test: %s (%s)", seed_dist, repo)

    apt_config = apt_config_for_chdist(args.chdist, args.chdist_base)
    _logger.info("archive metadata: %s", apt_config)

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
            branch_dirs = {}
            for checkout in (old_co, new_co):
                branch_dirs.update(
                    resolve_dependencies(seed_dist, checkout, collection_map)
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

            old_run = _one_side(
                args,
                "old",
                old_commit,
                old_co,
                work_dir,
                apt_config,
                branch_dirs,
                seed_dist,
            )
            new_run = _one_side(
                args,
                "new",
                new_commit,
                new_co,
                work_dir,
                apt_config,
                branch_dirs,
                seed_dist,
            )
    finally:
        if remove_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        elif args.keep:
            _logger.info("left working files in %s", work_dir)

    return format_diff(diff_runs(old_run, new_run))


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        format="germinate-diff: %(message)s",
        level=logging.INFO if args.verbose else logging.WARNING,
        stream=sys.stderr,
    )
    try:
        text = run(args)
    except (
        CollectionMapError,
        GerminateError,
        GitError,
        StructureError,
    ) as e:
        print("germinate-diff: error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("germinate-diff: interrupted", file=sys.stderr)
        return 130

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Command-line entry point for germidiff-mp.

Turns a Launchpad merge proposal into a germidiff report: work out which
seed collection and series it changes, fetch what germinating them needs,
make sure there is a chdist to read the archive from, and diff.

Everything here is read-only with respect to Launchpad.  Posting the report
back to the proposal is a separate step, on purpose: resolving a proposal
needs no credentials and can be run against anything, while commenting on
one needs an account and is hard to take back.
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

import argparse
import logging
import os
import re
import sys

from germidiff import VERSION, cli
from germidiff.chdist import ChdistError, ensure_chdist, update_chdist
from germidiff.launchpad import (
    LaunchpadError,
    git_url_for,
    load_merge_proposal,
)
from germidiff.runner import GerminateError
from germidiff.seedtree import (
    DEFAULT_SEED_SOURCE,
    SeedCache,
    SeedCacheError,
)
from germidiff.structure import StructureError
from germidiff.worktree import GitError, merge_base, resolve_ref

_logger = logging.getLogger("germidiff")

# Statuses where the proposal is no longer a question anyone is asking.
# Diffing one is a perfectly reasonable thing to do -- it is how you look at
# what a change did -- but worth saying out loud, since the usual reason to
# run this is to review something.
SETTLED_STATUSES = frozenset(
    {"Merged", "Rejected", "Superseded", "Work in progress"}
)

_MERGE_NUMBER = re.compile(r"/\+merge/(\d+)")

DESCRIPTION = """\
Show the consequences of the seed change a Launchpad merge proposal makes.

Reads the proposal to find which collection and series it targets, clones
what germinating them needs into a reusable cache, creates the chdist the
archive metadata comes from if it is not there already, and runs germidiff
over the change.  The report is written to standard output; nothing is
posted back to Launchpad.
"""

EPILOG = """\
The change is diffed from the merge base -- the commit the proposal branched
from -- rather than from the target branch's tip, so the report describes
what this proposal does and not what landed alongside it.  That is the same
comparison Launchpad's own diff shows.

The chdist is named for the series, suffixed for the components: the ubuntu
and platform collections germinate against main and restricted, so they use
a chdist called after the series alone, and flavours germinate against the
whole archive from one called SERIES-all.  Germinate takes its components
from the chdist and nothing else, so an existing chdist offering the wrong
ones is refused rather than used.

Those chdists are germidiff's own, kept under --chdist-base rather than in
the ~/.chdist the chdist tool uses, so that creating and updating them
cannot disturb any chdist you keep for yourself under the same name.

Exits 0 when the germinate runs succeeded, whether or not there were any
differences, and nonzero if anything went wrong.
"""


def _default_cache_dir():
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if not cache_home:
        cache_home = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(cache_home, "germidiff", "seeds")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="germidiff-mp",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + VERSION
    )
    parser.add_argument(
        "merge_proposal",
        metavar="MERGE-PROPOSAL",
        help="URL of the Launchpad merge proposal to diff",
    )

    parser.add_argument(
        "--cache-dir",
        metavar="DIR",
        default=_default_cache_dir(),
        help="directory of seed collection clones, reused between runs "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--seed-source",
        metavar="URL",
        default=DEFAULT_SEED_SOURCE,
        help="where to clone included collections from; the branch name's "
        "repository part is appended (default: %(default)s)",
    )
    parser.add_argument(
        "--chdist",
        metavar="NAME",
        help="use this chdist instead of the one the series and collection "
        "imply; it is taken as it stands rather than created or checked",
    )
    parser.add_argument(
        "--no-header",
        dest="header",
        action="store_false",
        default=True,
        help="leave off the line saying which proposal and archive the "
        "report came from",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch what is needed and report what would be germinated, "
        "without germinating it",
    )

    cli.add_chdist_options(parser)
    cli.add_analysis_options(parser)

    args = parser.parse_args(argv)
    if args.work_dir:
        args.keep = True
    return args


def _header(mp, branch, old, new, chdist, components):
    return (
        "germidiff of %s\n"
        "%s, %s..%s, against %s (%s)\n"
        % (
            mp.url,
            branch,
            old[:12],
            new[:12],
            chdist,
            " ".join(components),
        )
    )


def run(args):
    """Do the work.  Returns the text to print on stdout."""
    mp = load_merge_proposal(args.merge_proposal)
    _logger.info(
        "%s: %s -> %s", mp.url, mp.source_ref, mp.target_ref
    )
    if mp.status in SETTLED_STATUSES:
        _logger.warning(
            "%s is %s; diffing it anyway", mp.url, mp.status.lower()
        )

    branch = mp.branch
    cache = SeedCache(args.cache_dir, seed_source=args.seed_source)
    repo = cache.ensure(branch, url=git_url_for(mp.target_repo))

    target = resolve_ref(repo, mp.target_ref)
    match = _MERGE_NUMBER.search(mp.url)
    source = cache.fetch_ref(
        branch,
        git_url_for(mp.source_repo),
        mp.source_ref,
        "mp/%s" % (match.group(1) if match else "head"),
    )
    old = merge_base(repo, target, source)
    _logger.info(
        "%s at %s, merge base with %s at %s",
        mp.source_ref, source[:12], mp.target_ref, old[:12],
    )
    if old == source:
        _logger.warning(
            "%s is already contained in %s; there is nothing to diff",
            mp.source_ref, mp.target_ref,
        )

    # An include line can be added or dropped by the very change under test,
    # so both sides get a say in which collections have to be on hand.
    dependencies = cache.ensure_dependencies(branch, [old, source])
    for name, directory in sorted(dependencies.items()):
        _logger.info("holding %s at %s", name, directory)

    components = cli.components_for(args, mp.collection)
    if args.chdist:
        chdist = args.chdist
        _logger.info("using chdist %s as given", chdist)
        if args.update:
            update_chdist(chdist, args.chdist_base)
    else:
        chdist = ensure_chdist(
            mp.series,
            components,
            args.arch or cli.host_arch(),
            base=args.chdist_base,
            mirror=args.mirror,
            update=args.update,
            check_components=args.check_components,
        )

    header = _header(mp, branch, old, source, chdist, components)
    if args.dry_run:
        return header + "\n(--dry-run: not germinating)\n"

    diff_args = argparse.Namespace(**vars(args))
    diff_args.seed_repo = repo
    diff_args.old_ref = old
    diff_args.new_ref = source
    diff_args.chdist = chdist
    diff_args.seed_dist = branch
    # The chdist is settled: it has been created or taken as given, and
    # refreshed if it was going to be.  Doing it again would cost a second
    # apt-get update for nothing.
    diff_args.update = False

    text = cli.run(diff_args)
    if args.header:
        return header + "\n" + text
    return text


def main(argv=None):
    args = parse_args(argv)
    cli.configure_logging(args, "germidiff-mp")
    try:
        text = run(args)
    except (
        ChdistError,
        GerminateError,
        GitError,
        LaunchpadError,
        SeedCacheError,
        StructureError,
    ) as e:
        print("germidiff-mp: error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("germidiff-mp: interrupted", file=sys.stderr)
        return 130

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

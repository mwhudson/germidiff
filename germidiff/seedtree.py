"""A local cache of seed collection checkouts.

Diffing a merge proposal needs more than the branch it proposes: germinate
expands the collection under test together with every collection it
``include``s, and those have to come from somewhere.  This keeps them in a
directory of clones named for their branches -- ``ubuntu.resolute``,
``platform.resolute`` -- which is the layout seed branches are conventionally
checked out in, and the one germidiff already looks in for a collection's
siblings.

Reusing the directory between runs is the point: the clones are refreshed
with a fetch rather than made again, so running over a stream of merge
proposals pays for the seed history once.
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

import logging
import os

from germidiff.structure import parse_structure, parse_structure_text
from germidiff.worktree import GitError, git

__all__ = [
    "DEFAULT_SEED_SOURCE",
    "SeedCache",
    "SeedCacheError",
    "split_branch",
]

_logger = logging.getLogger("germidiff")

# Germinate's own default, over https rather than the anonymous git protocol.
# It is where the collections anyone actually includes live: every flavour's
# STRUCTURE includes platform and nothing else, and platform belongs to
# ~ubuntu-core-dev.  A flavour's own repo is not under here, but that is the
# collection under test, whose location the merge proposal states outright.
DEFAULT_SEED_SOURCE = (
    "https://git.launchpad.net/~ubuntu-core-dev/ubuntu-seeds/+git/"
)


class SeedCacheError(Exception):
    """A seed collection could not be fetched."""


def split_branch(branch):
    """Split a branch name into its repository and git branch.

    This is germinate's rule, and deliberately its oddity too: everything
    before the last dot names the repository and everything after it names
    the branch, so ``ubuntu.resolute`` is branch ``resolute`` of the
    ``ubuntu`` repo.  A name with no dot at all names a repository whose
    default branch is wanted.
    """
    if "." in branch:
        repository, git_branch = branch.rsplit(".", 1)
        return repository, git_branch
    return branch, None


class SeedCache:
    """A directory of seed collection clones, one per branch."""

    def __init__(self, directory, seed_source=None, urls=None):
        self.directory = os.path.abspath(os.path.expanduser(directory))
        self.seed_source = seed_source or DEFAULT_SEED_SOURCE
        if not self.seed_source.endswith("/"):
            self.seed_source += "/"
        self._urls = dict(urls or {})

    def path(self, branch):
        """Where a branch's checkout lives, whether or not it is there yet."""
        return os.path.join(self.directory, branch)

    def url_for(self, branch):
        """The git URL a branch is fetched from."""
        if branch in self._urls:
            return self._urls[branch]
        repository, _ = split_branch(branch)
        if repository in self._urls:
            return self._urls[repository]
        return self.seed_source + repository

    def ensure(self, branch, url=None):
        """Clone or refresh ``branch``, returning its checkout directory.

        The checkout is left at the branch's tip.  This is a cache we own
        outright, so a refresh is a hard reset: local state in it would only
        ever be a leftover from a previous run.
        """
        directory = self.path(branch)
        if url is None:
            url = self.url_for(branch)
        _, git_branch = split_branch(branch)

        try:
            if os.path.isdir(os.path.join(directory, ".git")):
                _logger.info("refreshing %s from %s", branch, url)
                git("remote", "set-url", "origin", url, repo=directory)
                git("fetch", "--quiet", "origin", repo=directory)
            else:
                _logger.info("cloning %s from %s", branch, url)
                os.makedirs(self.directory, exist_ok=True)
                git("clone", "--quiet", url, directory)

            if git_branch is not None:
                git(
                    "checkout", "--quiet", "--force", "-B", git_branch,
                    "refs/remotes/origin/%s" % git_branch,
                    repo=directory,
                )
        except GitError as e:
            raise SeedCacheError(
                "could not fetch seed collection %s from %s:\n%s"
                % (branch, url, e)
            )

        if not os.path.isfile(os.path.join(directory, "STRUCTURE")):
            raise SeedCacheError(
                "%s has no STRUCTURE file; %s does not look like a seed "
                "collection" % (directory, url)
            )
        return directory

    def fetch_ref(self, branch, url, ref, name):
        """Fetch one ref from another repo into ``branch``'s clone.

        Returns the commit it resolved to.  The proposal's source branch
        lives in a fork, and both sides of the diff have to be reachable from
        one repository for germidiff to make worktrees of them.
        """
        directory = self.path(branch)
        local = "refs/germidiff/%s" % name
        try:
            git(
                "fetch", "--quiet", "--force", url,
                "+%s:%s" % (ref, local),
                repo=directory,
            )
            return git("rev-parse", "--verify", local, repo=directory)
        except GitError as e:
            raise SeedCacheError(
                "could not fetch %s from %s:\n%s" % (ref, url, e)
            )

    def structure_at(self, branch, ref):
        """Read a branch's ``STRUCTURE`` file at ``ref``, without checkout.

        The two sides of a proposal can disagree about which collections are
        needed -- adding or dropping an ``include`` line is a perfectly
        ordinary seed change -- so both get read, and neither is the one the
        working tree happens to be at.
        """
        directory = self.path(branch)
        try:
            text = git("show", "%s:STRUCTURE" % ref, repo=directory)
        except GitError as e:
            raise SeedCacheError(
                "could not read STRUCTURE at %s in %s:\n%s"
                % (ref, directory, e)
            )
        return parse_structure_text(text)

    def ensure_dependencies(self, branch, refs):
        """Fetch every collection ``branch`` includes, at any of ``refs``.

        Returns the mapping of branch name to directory.  Nested collections
        (``ubuntu.resolute/languages``) are skipped: they are directories
        inside their parent and arrive with it.
        """
        directories = {}
        seen = {branch}
        queue = []

        for ref in refs:
            _, includes = self.structure_at(branch, ref)
            queue.extend(includes)

        while queue:
            included = queue.pop(0)
            if included in seen:
                continue
            seen.add(included)
            if "/" in included:
                _logger.debug(
                    "%s is nested inside %s and needs no clone of its own",
                    included, included.split("/", 1)[0],
                )
                continue
            directory = self.ensure(included)
            directories[included] = directory
            _, more = parse_structure(
                os.path.join(directory, "STRUCTURE")
            )
            queue.extend(more)

        return directories

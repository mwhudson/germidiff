"""Throwaway git worktrees for the two refs under test."""

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
import subprocess

__all__ = [
    "GitError",
    "check_repo",
    "describe_head",
    "resolve_ref",
    "worktrees",
]

_logger = logging.getLogger("germidiff")


class GitError(Exception):
    """A git command failed."""


def _git(repo, *args):
    command = ["git", "-C", repo] + list(args)
    proc = subprocess.run(
        command, capture_output=True, encoding="UTF-8", errors="replace"
    )
    if proc.returncode != 0:
        raise GitError(
            "%s failed with exit status %d:\n%s"
            % (" ".join(command), proc.returncode, proc.stderr.strip())
        )
    return proc.stdout.strip()


def check_repo(repo):
    """Raise GitError unless ``repo`` is a git working tree."""
    if not os.path.isdir(repo):
        raise GitError("%s is not a directory" % repo)
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise GitError("%s is not a git working tree" % repo)


def resolve_ref(repo, ref):
    """Resolve ``ref`` to a commit hash, raising GitError if it is unknown."""
    try:
        return _git(repo, "rev-parse", "--verify", "%s^{commit}" % ref)
    except GitError:
        raise GitError("%s: no such commit in %s" % (ref, repo))


def describe_head(directory):
    """Best-effort one-line description of a checkout's current commit.

    Used only for the run summary we write to stderr, so a directory that
    isn't a git checkout at all is not an error.
    """
    try:
        return _git(directory, "log", "-1", "--format=%h %s")
    except (GitError, OSError):
        return "not a git checkout"


class _Worktrees:
    """Context manager adding worktrees and removing them afterwards."""

    def __init__(self, repo, checkouts, keep=False):
        self._repo = repo
        self._checkouts = checkouts
        self._keep = keep
        self._added = []

    def __enter__(self):
        try:
            for path, ref in self._checkouts:
                _logger.debug("adding worktree %s at %s", path, ref)
                _git(self._repo, "worktree", "add", "--detach", path, ref)
                self._added.append(path)
        except Exception:
            self._cleanup()
            raise
        return [path for path, _ in self._checkouts]

    def __exit__(self, exc_type, exc_value, exc_tb):
        if not self._keep:
            self._cleanup()
        return False

    def _cleanup(self):
        for path in reversed(self._added):
            try:
                _git(self._repo, "worktree", "remove", "--force", path)
            except GitError as e:
                _logger.warning("could not remove worktree %s: %s", path, e)
        self._added = []


def worktrees(repo, checkouts, keep=False):
    """Create detached worktrees of ``repo``.

    ``checkouts`` is a sequence of ``(path, ref)`` pairs.  Returns a context
    manager yielding the list of paths, which are removed again on exit unless
    ``keep`` is true.
    """
    return _Worktrees(repo, list(checkouts), keep=keep)

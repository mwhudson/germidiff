"""Tests for running git."""

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

from unittest import mock

from germidiff.worktree import GitError, git
from tests.helpers import GitTestCase, TestCase


class TestGit(GitTestCase, TestCase):
    def test_a_missing_git_is_a_git_error(self):
        # git is a hard requirement, so its absence must fail noisily as
        # a GitError rather than escape as a FileNotFoundError.
        with mock.patch(
            "subprocess.run", side_effect=FileNotFoundError("git")
        ):
            with self.assertRaises(GitError) as raised:
                git("status")
        self.assertIn("could not run git", str(raised.exception))

    def test_a_failing_git_carries_its_own_message(self):
        repo = self.make_repo(self.make_temp_dir())
        with self.assertRaises(GitError) as raised:
            git("rev-parse", "--verify", "no-such-ref", repo=repo)
        self.assertIn("no-such-ref", str(raised.exception))

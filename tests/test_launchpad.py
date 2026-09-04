"""Tests for reading a merge proposal from Launchpad."""

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

from germidiff.launchpad import (
    API_ROOT,
    LaunchpadError,
    api_url_for,
    git_url_for,
    load_merge_proposal,
    repo_name,
)
from tests.helpers import TestCase

WEB_URL = (
    "https://code.launchpad.net/~gjolly/ubuntu-seeds/+git/ubuntu/+merge/509106"
)
API_URL = (
    "https://api.launchpad.net/devel/~gjolly/ubuntu-seeds/+git/ubuntu"
    "/+merge/509106"
)


def proposal(**overrides):
    data = {
        "resource_type_link":
            "https://api.launchpad.net/devel/#branch_merge_proposal",
        "web_link": WEB_URL,
        "source_git_repository_link":
            API_ROOT + "~gjolly/ubuntu-seeds/+git/ubuntu",
        "source_git_path": "refs/heads/fix/cloud-minimal/add_curl",
        "target_git_repository_link":
            API_ROOT + "~ubuntu-core-dev/ubuntu-seeds/+git/ubuntu",
        "target_git_path": "refs/heads/resolute",
        "queue_status": "Needs review",
        "commit_message": "seed curl explicitly",
    }
    data.update(overrides)
    return data


class TestApiUrlFor(TestCase):
    def test_translates_web_url(self):
        self.assertEqual(API_URL, api_url_for(WEB_URL))

    def test_accepts_api_url(self):
        self.assertEqual(API_URL, api_url_for(API_URL))

    def test_ignores_trailing_slash_and_space(self):
        self.assertEqual(API_URL, api_url_for("  " + WEB_URL + "/  "))

    def test_rejects_bare_number(self):
        with self.assertRaises(LaunchpadError) as raised:
            api_url_for("509106")
        self.assertIn("no way to look one up", str(raised.exception))

    def test_rejects_url_that_is_not_a_proposal(self):
        with self.assertRaises(LaunchpadError) as raised:
            api_url_for(
                "https://code.launchpad.net/~ubuntu-core-dev/ubuntu-seeds"
                "/+git/ubuntu"
            )
        self.assertIn("does not name a merge proposal", str(raised.exception))

    def test_rejects_url_elsewhere(self):
        with self.assertRaises(LaunchpadError):
            api_url_for("https://example.org/+merge/1")

    def test_rejects_empty(self):
        with self.assertRaises(LaunchpadError):
            api_url_for("   ")


class TestRepoName(TestCase):
    def test_takes_last_segment(self):
        self.assertEqual(
            "platform",
            repo_name(
                API_ROOT + "~ubuntu-core-dev/ubuntu-seeds/+git/platform"
            ),
        )

    def test_ignores_trailing_slash(self):
        self.assertEqual("ubuntu", repo_name(API_ROOT + "~x/y/+git/ubuntu/"))


class TestGitUrlFor(TestCase):
    def test_swaps_api_root_for_git_root(self):
        self.assertEqual(
            "https://git.launchpad.net/~ubuntu-core-dev/ubuntu-seeds"
            "/+git/ubuntu",
            git_url_for(
                API_ROOT + "~ubuntu-core-dev/ubuntu-seeds/+git/ubuntu"
            ),
        )

    def test_rejects_url_elsewhere(self):
        with self.assertRaises(LaunchpadError):
            git_url_for("https://example.org/repo")


class TestLoadMergeProposal(TestCase):
    def load(self, data, reference=WEB_URL):
        self.asked = []

        def fetch(url):
            self.asked.append(url)
            return data

        return load_merge_proposal(reference, fetch=fetch)

    def test_reads_the_api_url(self):
        self.load(proposal())
        self.assertEqual([API_URL], self.asked)

    def test_collection_comes_from_the_target(self):
        # The proposal arrives from a fork, which may be named anything at
        # all; what it proposes to change is the target.
        mp = self.load(
            proposal(
                source_git_repository_link=(
                    API_ROOT + "~someone/ubuntu-seeds/+git/my-seed-fork"
                )
            )
        )
        self.assertEqual("ubuntu", mp.collection)

    def test_series_is_the_target_branch(self):
        self.assertEqual("resolute", self.load(proposal()).series)

    def test_branch_joins_collection_and_series(self):
        self.assertEqual("ubuntu.resolute", self.load(proposal()).branch)

    def test_keeps_status_and_message(self):
        mp = self.load(proposal())
        self.assertEqual("Needs review", mp.status)
        self.assertEqual("seed curl explicitly", mp.commit_message)

    def test_url_falls_back_to_the_api_url(self):
        self.assertEqual(API_URL, self.load(proposal(web_link=None)).url)

    def test_rejects_bazaar_proposal(self):
        data = proposal(
            source_git_repository_link=None,
            target_git_repository_link=None,
            source_branch_link="https://api.launchpad.net/devel/~x/y/z",
        )
        with self.assertRaises(LaunchpadError) as raised:
            self.load(data)
        self.assertIn("not a git merge proposal", str(raised.exception))

    def test_rejects_something_that_is_not_a_proposal(self):
        with self.assertRaises(LaunchpadError) as raised:
            self.load(
                {
                    "resource_type_link":
                        "https://api.launchpad.net/devel/#git_repository",
                    "name": "ubuntu",
                }
            )
        self.assertIn("not a merge proposal", str(raised.exception))

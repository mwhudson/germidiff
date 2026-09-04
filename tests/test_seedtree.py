"""Tests for the local cache of seed collection checkouts."""

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

import os

from germidiff.seedtree import (
    DEFAULT_SEED_SOURCE,
    SeedCache,
    SeedCacheError,
    split_branch,
)
from tests.helpers import GitTestCase


class TestSplitBranch(GitTestCase):
    def test_splits_on_the_last_dot(self):
        self.assertEqual(
            ("ubuntu", "resolute"), split_branch("ubuntu.resolute")
        )

    def test_keeps_earlier_dots_in_the_repository(self):
        self.assertEqual(("a.b", "c"), split_branch("a.b.c"))

    def test_no_dot_means_the_default_branch(self):
        self.assertEqual(("platform", None), split_branch("platform"))


class TestUrlFor(GitTestCase):
    def test_appends_the_repository_to_the_seed_source(self):
        cache = SeedCache("/nowhere")
        self.assertEqual(
            DEFAULT_SEED_SOURCE + "platform",
            cache.url_for("platform.resolute"),
        )

    def test_adds_a_missing_slash_to_the_seed_source(self):
        cache = SeedCache(
            "/nowhere", seed_source="https://example.org/seeds"
        )
        self.assertEqual(
            "https://example.org/seeds/platform",
            cache.url_for("platform.resolute"),
        )

    def test_override_by_branch_wins(self):
        cache = SeedCache(
            "/nowhere", urls={"platform.resolute": "https://example.org/p"}
        )
        self.assertEqual(
            "https://example.org/p", cache.url_for("platform.resolute")
        )

    def test_override_by_repository_applies_to_every_series(self):
        cache = SeedCache(
            "/nowhere", urls={"kubuntu": "https://example.org/k"}
        )
        self.assertEqual(
            "https://example.org/k", cache.url_for("kubuntu.questing")
        )


class TestSeedCache(GitTestCase):
    def setUp(self):
        self.temp_dir = self.make_temp_dir()
        self.cache = SeedCache(os.path.join(self.temp_dir, "cache"))

    def make_collection(self, name, structure="base:\n", branch="questing"):
        """An upstream repo with one commit on ``branch``."""
        repo = self.make_repo(os.path.join(self.temp_dir, "upstream", name))
        self.write(os.path.join(repo, "STRUCTURE"), structure)
        self.git(repo, "checkout", "-q", "-b", branch)
        self.commit(repo, "initial")
        return repo

    def test_clones_at_the_branch_the_name_says(self):
        upstream = self.make_collection("thing", branch="questing")
        self.git(upstream, "checkout", "-q", "-b", "other")
        self.write(os.path.join(upstream, "STRUCTURE"), "other:\n")
        self.commit(upstream, "on the other branch")

        directory = self.cache.ensure("thing.questing", url=upstream)

        self.assertEqual(self.cache.path("thing.questing"), directory)
        with open(os.path.join(directory, "STRUCTURE")) as f:
            self.assertEqual("base:\n", f.read())

    def test_refreshes_an_existing_clone(self):
        upstream = self.make_collection("thing")
        self.cache.ensure("thing.questing", url=upstream)
        self.write(os.path.join(upstream, "STRUCTURE"), "base:\nmore:\n")
        self.commit(upstream, "second")

        directory = self.cache.ensure("thing.questing", url=upstream)

        with open(os.path.join(directory, "STRUCTURE")) as f:
            self.assertIn("more:", f.read())

    def test_refresh_discards_local_changes(self):
        # The cache is ours; anything in it is a leftover, not someone's work.
        upstream = self.make_collection("thing")
        directory = self.cache.ensure("thing.questing", url=upstream)
        self.write(os.path.join(directory, "STRUCTURE"), "scribbled:\n")

        self.cache.ensure("thing.questing", url=upstream)

        with open(os.path.join(directory, "STRUCTURE")) as f:
            self.assertEqual("base:\n", f.read())

    def test_missing_repo_says_what_it_tried(self):
        with self.assertRaises(SeedCacheError) as raised:
            self.cache.ensure(
                "thing.questing", url=os.path.join(self.temp_dir, "nope")
            )
        self.assertIn("thing.questing", str(raised.exception))
        self.assertIn("nope", str(raised.exception))

    def test_repo_without_structure_is_refused(self):
        repo = self.make_repo(os.path.join(self.temp_dir, "upstream", "empty"))
        self.git(repo, "checkout", "-q", "-b", "questing")
        self.write(os.path.join(repo, "README"), "not a seed collection\n")
        self.commit(repo, "initial")

        with self.assertRaises(SeedCacheError) as raised:
            self.cache.ensure("empty.questing", url=repo)
        self.assertIn("no STRUCTURE", str(raised.exception))

    def test_fetch_ref_brings_a_fork_into_the_clone(self):
        upstream = self.make_collection("thing")
        fork = os.path.join(self.temp_dir, "fork")
        self.git(self.temp_dir, "clone", "-q", upstream, fork)
        self.git(fork, "checkout", "-q", "-b", "proposed")
        self.write(os.path.join(fork, "STRUCTURE"), "base:\nproposed:\n")
        expected = self.commit(fork, "propose something")

        self.cache.ensure("thing.questing", url=upstream)
        sha = self.cache.fetch_ref(
            "thing.questing", fork, "refs/heads/proposed", "mp/1"
        )

        self.assertEqual(expected, sha)
        seeds, _ = self.cache.structure_at("thing.questing", sha)
        self.assertEqual(["base", "proposed"], seeds)

    def test_fetch_ref_reports_a_ref_that_is_not_there(self):
        upstream = self.make_collection("thing")
        self.cache.ensure("thing.questing", url=upstream)
        with self.assertRaises(SeedCacheError) as raised:
            self.cache.fetch_ref(
                "thing.questing", upstream, "refs/heads/absent", "mp/1"
            )
        self.assertIn("refs/heads/absent", str(raised.exception))

    def test_structure_at_reads_a_ref_that_is_not_checked_out(self):
        upstream = self.make_collection("thing")
        first = self.git(upstream, "rev-parse", "HEAD")
        self.write(os.path.join(upstream, "STRUCTURE"), "base:\nlater:\n")
        self.commit(upstream, "second")
        self.cache.ensure("thing.questing", url=upstream)

        self.assertEqual(
            ["base"], self.cache.structure_at("thing.questing", first)[0]
        )
        self.assertEqual(
            ["base", "later"],
            self.cache.structure_at("thing.questing", "HEAD")[0],
        )


class TestEnsureDependencies(GitTestCase):
    def setUp(self):
        self.temp_dir = self.make_temp_dir()
        self.upstream = os.path.join(self.temp_dir, "upstream")
        self.cache = SeedCache(
            os.path.join(self.temp_dir, "cache"),
            seed_source="%s/" % self.upstream,
        )

    def make_collection(self, name, structure, branch="questing"):
        repo = self.make_repo(os.path.join(self.upstream, name))
        self.write(os.path.join(repo, "STRUCTURE"), structure)
        self.git(repo, "checkout", "-q", "-b", branch)
        self.commit(repo, "initial")
        return repo

    def test_follows_include_lines_transitively(self):
        self.make_collection("base", "required:\n")
        self.make_collection(
            "platform", "include base.questing\nminimal: required\n"
        )
        self.make_collection(
            "thing", "include platform.questing\ndesktop: minimal\n"
        )
        self.cache.ensure("thing.questing")

        found = self.cache.ensure_dependencies("thing.questing", ["HEAD"])

        self.assertEqual(
            ["base.questing", "platform.questing"], sorted(found)
        )

    def test_takes_the_union_over_both_sides(self):
        # A proposal that drops an include line still has to germinate the
        # side that has it.
        self.make_collection("platform", "minimal:\n")
        self.make_collection("extras", "more:\n")
        thing = self.make_collection(
            "thing", "include platform.questing\ninclude extras.questing\nx:\n"
        )
        old = self.git(thing, "rev-parse", "HEAD")
        self.write(
            os.path.join(thing, "STRUCTURE"),
            "include platform.questing\nx:\n",
        )
        new = self.commit(thing, "drop extras")
        self.cache.ensure("thing.questing")

        found = self.cache.ensure_dependencies("thing.questing", [old, new])

        self.assertEqual(
            ["extras.questing", "platform.questing"], sorted(found)
        )

    def test_nested_collections_come_with_their_parent(self):
        self.make_collection(
            "thing", "include thing.questing/languages\nx:\n"
        )
        self.cache.ensure("thing.questing")

        found = self.cache.ensure_dependencies("thing.questing", ["HEAD"])

        self.assertEqual({}, found)

    def test_reports_a_collection_it_cannot_find(self):
        self.make_collection("thing", "include absent.questing\nx:\n")
        self.cache.ensure("thing.questing")

        with self.assertRaises(SeedCacheError) as raised:
            self.cache.ensure_dependencies("thing.questing", ["HEAD"])
        self.assertIn("absent.questing", str(raised.exception))

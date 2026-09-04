"""Tests for spotting dependencies that only a metapackage rebuild removes."""

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

from germidiff.listfile import parse_why
from germidiff.metapackage import PendingEdge, pending_edges, seed_headers
from germidiff.retention import Retained
from tests.helpers import TestCase


class MetapackageTestCase(TestCase):
    """Two germinate output directories, written by hand.

    The interesting inputs are the seed entry lists, and writing them
    directly says what each test is about far more plainly than germinating
    something that happens to produce them.
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()
        self.old = os.path.join(self.temp_dir, "old")
        self.new = os.path.join(self.temp_dir, "new")

    def table(self, packages):
        rows = "".join(
            "%-20s | %-10s | %-20s | x | 0 | 0\n" % (pkg, pkg, "Ubuntu x seed")
            for pkg in packages
        )
        return (
            "%-20s | %-10s | %-20s | x | 0 | 0\n"
            % ("Package", "Source", "Why")
            + "-" * 60
            + "\n"
            + rows
        )

    def seed(self, out_dir, name, entries=(), recommends=(), seedtext=None):
        self.write(
            os.path.join(out_dir, "%s.seed" % name), self.table(entries)
        )
        self.write(
            os.path.join(out_dir, "%s.seed-recommends" % name),
            self.table(recommends),
        )
        if seedtext is not None:
            self.write(
                os.path.join(out_dir, "%s.seedtext" % name), seedtext
            )

    def retained(self, package, why):
        return Retained(package, [parse_why(why)], ["desktop"])


class TestSeedHeaders(MetapackageTestCase):
    def test_reads_both_headers(self):
        self.seed(
            self.new,
            "desktop",
            seedtext="Task-Metapackage: ubuntu-desktop\n"
            "Task-Seeds: desktop-common\n"
            " * firefox\n",
        )
        self.assertEqual(
            (["desktop-common"], "ubuntu-desktop"),
            seed_headers(self.new, "desktop"),
        )

    def test_a_seed_with_no_headers(self):
        self.seed(self.new, "desktop", seedtext=" * firefox\n")
        self.assertEqual(([], None), seed_headers(self.new, "desktop"))

    def test_a_seed_with_no_text_at_all(self):
        self.assertEqual(([], None), seed_headers(self.new, "absent"))


class TestPendingEdges(MetapackageTestCase):
    def test_a_dropped_depends_is_pending(self):
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        edges = pending_edges(
            self.old,
            self.new,
            ["desktop"],
            [self.retained("firefox", "ubuntu-desktop")],
        )

        self.assertEqual(
            [PendingEdge("ubuntu-desktop", "firefox", "desktop", "Depends")],
            edges,
        )
        self.assertEqual("Depends", edges[0].field)

    def test_a_dropped_recommends_is_pending_too(self):
        # The metapackage's Recommends is generated from the seed's
        # seed-recommends exactly as its Depends is from the entries, and
        # germinate follows Recommends, so this hides a change just as
        # completely.
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop"],
            recommends=["hexchat"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        edges = pending_edges(
            self.old,
            self.new,
            ["desktop"],
            [self.retained("hexchat", "ubuntu-desktop (Recommends)")],
        )

        self.assertEqual(
            [
                PendingEdge(
                    "ubuntu-desktop", "hexchat", "desktop", "Recommends"
                )
            ],
            edges,
        )
        self.assertEqual("Recommends", edges[0].field)

    def test_a_package_moved_between_the_two_lists_is_not_pending(self):
        # It has not been dropped; it comes back as a Recommends.  Reporting
        # it would tell the reviewer their change does something it does not.
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            recommends=["firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "ubuntu-desktop")],
            ),
        )

    def test_a_package_the_seed_still_names_is_not_pending(self):
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "ubuntu-desktop")],
            ),
        )

    def test_a_holder_that_is_not_one_of_our_metapackages_is_left_alone(self):
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "some-other-package")],
            ),
        )

    def test_a_build_dependency_is_not_a_metapackage_edge(self):
        # Only Depends and Recommends are generated from the seeds; nothing
        # a rebuild does would rewrite a build-dependency.
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "ubuntu-desktop (Build-Depend)")],
            ),
        )

    def test_a_seeded_reason_has_no_holder_to_cut(self):
        self.seed(
            self.old,
            "desktop",
            entries=["ubuntu-desktop", "firefox"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )
        self.seed(
            self.new,
            "desktop",
            entries=["ubuntu-desktop"],
            seedtext="Task-Metapackage: ubuntu-desktop\n",
        )

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "Ubuntu desktop seed")],
            ),
        )

    def test_task_seeds_are_part_of_the_metapackage(self):
        # A metapackage is built from its seed plus everything the seed's
        # Task-Seeds header names, so dropping the entry from one of those
        # counts too.
        text = "Task-Metapackage: ubuntu-desktop\nTask-Seeds: desktop-common\n"
        self.seed(self.old, "desktop", entries=["ubuntu-desktop"],
                  seedtext=text)
        self.seed(self.old, "desktop-common", entries=["firefox"])
        self.seed(self.new, "desktop", entries=["ubuntu-desktop"],
                  seedtext=text)
        self.seed(self.new, "desktop-common", entries=[])

        edges = pending_edges(
            self.old,
            self.new,
            ["desktop"],
            [self.retained("firefox", "ubuntu-desktop")],
        )

        self.assertEqual(["firefox"], [e.package for e in edges])

    def test_the_metapackage_name_can_be_inferred(self):
        # No Task-Metapackage header: the name has to end in -<seed> and the
        # metapackage has to be seeded there, two signals rather than a guess.
        self.seed(
            self.old, "desktop", entries=["ubuntu-desktop", "firefox"]
        )
        self.seed(self.new, "desktop", entries=["ubuntu-desktop"])

        edges = pending_edges(
            self.old,
            self.new,
            ["desktop"],
            [self.retained("firefox", "ubuntu-desktop")],
        )

        self.assertEqual(["ubuntu-desktop"], [e.metapackage for e in edges])

    def test_a_metapackage_seeded_as_a_recommends_still_counts(self):
        self.seed(
            self.old,
            "desktop",
            entries=["firefox"],
            recommends=["ubuntu-desktop"],
        )
        self.seed(self.new, "desktop", recommends=["ubuntu-desktop"])

        edges = pending_edges(
            self.old,
            self.new,
            ["desktop"],
            [self.retained("firefox", "ubuntu-desktop")],
        )

        self.assertEqual(["ubuntu-desktop"], [e.metapackage for e in edges])

    def test_an_inferred_name_needs_the_metapackage_to_be_seeded(self):
        self.seed(self.old, "desktop", entries=["firefox"])
        self.seed(self.new, "desktop", entries=[])

        self.assertEqual(
            [],
            pending_edges(
                self.old,
                self.new,
                ["desktop"],
                [self.retained("firefox", "ubuntu-desktop")],
            ),
        )


class TestPendingEdgeIdentity(TestCase):
    def test_the_relationship_is_part_of_the_edge(self):
        # Both would have to be cut to germinate as though the metapackages
        # had been rebuilt.
        self.assertNotEqual(
            PendingEdge("ubuntu-desktop", "firefox", "desktop", "Depends"),
            PendingEdge("ubuntu-desktop", "firefox", "desktop", "Recommends"),
        )

    def test_the_seed_is_not(self):
        # The same dependency reached through two seeds is one dependency.
        self.assertEqual(
            PendingEdge("ubuntu-desktop", "firefox", "desktop"),
            PendingEdge("ubuntu-desktop", "firefox", "desktop-common"),
        )

    def test_edges_compare_to_other_things_without_blowing_up(self):
        self.assertNotEqual(
            PendingEdge("ubuntu-desktop", "firefox", "desktop"), "firefox"
        )

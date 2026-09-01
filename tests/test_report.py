"""Tests for the plain-text report."""

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

from germidiff.diff import NEW_SEED, REMOVED_SEED, Diff, SeedDiff
from germidiff.report import NO_CHANGES, format_diff
from tests.helpers import TestCase


class TestFormatDiff(TestCase):
    def test_no_changes(self):
        diff = Diff(SeedDiff("global", [], []), [SeedDiff("base", [], [])])
        self.assertEqual(NO_CHANGES + "\n", format_diff(diff))

    def test_full_shape(self):
        diff = Diff(
            SeedDiff("global", ["newpackage"], ["oldpackage"]),
            [
                SeedDiff("desktop", ["newpackage"], ["oldpackage"]),
                SeedDiff("unchanged", [], []),
                SeedDiff(
                    "server",
                    ["anotherpackage", "yetanotherpackage"],
                    [],
                    presence=NEW_SEED,
                ),
                SeedDiff(
                    "oldseed", [], ["somepackage"], presence=REMOVED_SEED
                ),
            ],
        )
        self.assertEqual(
            "**global**\n"
            "+newpackage\n"
            "-oldpackage\n"
            "\n"
            "**desktop**\n"
            "+newpackage\n"
            "-oldpackage\n"
            "\n"
            "**server** (new seed)\n"
            "+anotherpackage\n"
            "+yetanotherpackage\n"
            "\n"
            "**oldseed** (removed seed)\n"
            "-somepackage\n",
            format_diff(diff),
        )

    def test_empty_global_section_says_so(self):
        diff = Diff(
            SeedDiff("global", [], []),
            [SeedDiff("desktop", [], ["moved"])],
        )
        self.assertEqual(
            "**global**\n"
            "(no net change across all seeds)\n"
            "\n"
            "**desktop**\n"
            "-moved\n",
            format_diff(diff),
        )

    def test_removed_seed_whose_packages_all_survive_is_summarised(self):
        # The case that motivated this: a seed removed, nothing actually
        # leaving the archive, and 60-odd "-" lines saying so at length.
        diff = Diff(
            SeedDiff("global", [], []),
            [
                SeedDiff(
                    "build-essential",
                    [],
                    ["gcc", "g++", "make"],
                    presence=REMOVED_SEED,
                    left=[],
                )
            ],
        )
        self.assertEqual(
            "**global**\n"
            "(no net change across all seeds)\n"
            "\n"
            "**build-essential** (removed seed)\n"
            "3 packages, all still pulled in by other seeds\n",
            format_diff(diff),
        )

    def test_removed_seed_leads_with_what_actually_left(self):
        diff = Diff(
            SeedDiff("global", [], ["gone"]),
            [
                SeedDiff(
                    "oldseed",
                    [],
                    ["gone", "kept", "alsokept"],
                    presence=REMOVED_SEED,
                    left=["gone"],
                )
            ],
        )
        self.assertIn(
            "**oldseed** (removed seed)\n"
            "-gone\n"
            "and 2 more, still pulled in by other seeds\n",
            format_diff(diff),
        )

    def test_new_seed_summarises_packages_already_present(self):
        diff = Diff(
            SeedDiff("global", ["fresh"], []),
            [
                SeedDiff(
                    "server",
                    ["fresh", "existing"],
                    [],
                    presence=NEW_SEED,
                    entered=["fresh"],
                )
            ],
        )
        self.assertIn(
            "**server** (new seed)\n"
            "+fresh\n"
            "and 1 more, already pulled in by other seeds\n",
            format_diff(diff),
        )

    def test_an_empty_new_seed_says_so(self):
        diff = Diff(
            SeedDiff("global", [], []),
            [SeedDiff("empty", [], [], presence=NEW_SEED)],
        )
        self.assertIn("**empty** (new seed)\nno packages\n", format_diff(diff))

    def test_whole_seed_lists_restores_the_full_listing(self):
        diff = Diff(
            SeedDiff("global", [], []),
            [
                SeedDiff(
                    "build-essential",
                    [],
                    ["gcc", "make"],
                    presence=REMOVED_SEED,
                    left=[],
                )
            ],
        )
        self.assertIn(
            "**build-essential** (removed seed)\n-gcc\n-make\n",
            format_diff(diff, whole_seed_lists=True),
        )

    def test_packages_are_sorted_with_additions_first(self):
        diff = Diff(
            SeedDiff("global", ["b", "a"], ["d", "c"]),
            [],
        )
        self.assertEqual("**global**\n+a\n+b\n-c\n-d\n", format_diff(diff))

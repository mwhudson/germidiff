"""Tests for the plain-text report."""

from germinate_diff.diff import NEW_SEED, REMOVED_SEED, Diff, SeedDiff
from germinate_diff.report import NO_CHANGES, format_diff
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

    def test_packages_are_sorted_with_additions_first(self):
        diff = Diff(
            SeedDiff("global", ["b", "a"], ["d", "c"]),
            [],
        )
        self.assertEqual("**global**\n+a\n+b\n-c\n-d\n", format_diff(diff))

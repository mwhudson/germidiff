"""Tests for diffing two germinate runs."""

from germinate_diff.diff import NEW_SEED, REMOVED_SEED, diff_runs
from germinate_diff.runner import GerminateRun
from tests.helpers import TestCase


def make_run(label, seeds, seed_names=None):
    seeds = {name: set(packages) for name, packages in seeds.items()}
    if seed_names is None:
        seed_names = list(seeds)
    return GerminateRun(label, "%s-commit" % label, seed_names, seeds)


class TestDiffRuns(TestCase):
    def by_name(self, diff):
        return {seed.name: seed for seed in diff.seeds}

    def test_added_and_removed_packages(self):
        old = make_run("old", {"desktop": ["a", "b"]})
        new = make_run("new", {"desktop": ["b", "c"]})
        diff = diff_runs(old, new)
        desktop = self.by_name(diff)["desktop"]
        self.assertEqual(["c"], desktop.added)
        self.assertEqual(["a"], desktop.removed)
        self.assertIsNone(desktop.presence)
        self.assertTrue(desktop.changed)

    def test_unchanged_seed_is_not_changed(self):
        old = make_run("old", {"desktop": ["a"]})
        new = make_run("new", {"desktop": ["a"]})
        diff = diff_runs(old, new)
        self.assertFalse(diff.changed)
        self.assertEqual([], diff.changed_seeds)

    def test_new_seed_is_labelled_and_all_added(self):
        old = make_run("old", {"base": ["a"]})
        new = make_run("new", {"base": ["a"], "server": ["x", "y"]})
        diff = diff_runs(old, new)
        server = self.by_name(diff)["server"]
        self.assertEqual(NEW_SEED, server.presence)
        self.assertEqual(["x", "y"], server.added)
        self.assertEqual([], server.removed)

    def test_removed_seed_is_labelled_and_all_removed(self):
        old = make_run("old", {"base": ["a"], "oldseed": ["z"]})
        new = make_run("new", {"base": ["a"]})
        diff = diff_runs(old, new)
        removed = self.by_name(diff)["oldseed"]
        self.assertEqual(REMOVED_SEED, removed.presence)
        self.assertEqual([], removed.added)
        self.assertEqual(["z"], removed.removed)

    def test_empty_new_seed_is_still_reported(self):
        old = make_run("old", {"base": ["a"]})
        new = make_run("new", {"base": ["a"], "empty": []})
        diff = diff_runs(old, new)
        empty = self.by_name(diff)["empty"]
        self.assertTrue(empty.changed)
        self.assertIn(empty, diff.changed_seeds)

    def test_global_diff_is_the_union_across_seeds(self):
        old = make_run("old", {"base": ["a"], "desktop": ["b"]})
        new = make_run("new", {"base": ["a"], "desktop": ["b", "c"]})
        diff = diff_runs(old, new)
        self.assertEqual(["c"], diff.global_diff.added)
        self.assertEqual([], diff.global_diff.removed)

    def test_package_still_pulled_in_elsewhere_is_no_net_removal(self):
        # "a" leaves desktop but is still in base, so the archive as a whole
        # is unchanged even though the desktop seed shrank.
        old = make_run("old", {"base": ["a"], "desktop": ["a", "b"]})
        new = make_run("new", {"base": ["a"], "desktop": ["b"]})
        diff = diff_runs(old, new)
        self.assertEqual(["a"], self.by_name(diff)["desktop"].removed)
        self.assertEqual([], diff.global_diff.added)
        self.assertEqual([], diff.global_diff.removed)
        self.assertFalse(diff.global_diff.changed)
        self.assertTrue(diff.changed)

    def test_global_counts_removed_seeds(self):
        old = make_run("old", {"base": ["a"], "oldseed": ["z"]})
        new = make_run("new", {"base": ["a"]})
        diff = diff_runs(old, new)
        self.assertEqual(["z"], diff.global_diff.removed)

    def test_removed_seed_separates_real_losses_from_moved_ones(self):
        # The seed's whole expansion is "removed" from it, but only what left
        # the archive is a real loss; the rest just changed which seed
        # accounts for it.
        old = make_run("old", {"base": ["a"], "gone": ["a", "b", "c"]})
        new = make_run("new", {"base": ["a", "b"]})
        diff = diff_runs(old, new)
        gone = self.by_name(diff)["gone"]
        self.assertEqual(["a", "b", "c"], gone.removed)
        self.assertEqual(["c"], gone.left)
        self.assertEqual(2, gone.elsewhere)
        self.assertEqual(["c"], diff.global_diff.removed)

    def test_new_seed_separates_real_arrivals_from_moved_ones(self):
        old = make_run("old", {"base": ["a", "b"]})
        new = make_run("new", {"base": ["a"], "fresh": ["b", "c"]})
        diff = diff_runs(old, new)
        fresh = self.by_name(diff)["fresh"]
        self.assertEqual(["b", "c"], fresh.added)
        self.assertEqual(["c"], fresh.entered)
        self.assertEqual(1, fresh.elsewhere)

    def test_an_unchanged_seed_keeps_its_whole_delta(self):
        # Only added and removed seeds are summarised; for a seed present in
        # both runs the +/- lines are the change itself.
        old = make_run("old", {"base": ["a"], "desktop": ["x"]})
        new = make_run("new", {"base": ["a", "x"], "desktop": []})
        diff = diff_runs(old, new)
        desktop = self.by_name(diff)["desktop"]
        self.assertEqual(["x"], desktop.removed)
        self.assertEqual(["x"], desktop.left)

    def test_report_order_is_new_structure_then_removed_seeds(self):
        old = make_run(
            "old", {"base": [], "gone": [], "desktop": []},
            seed_names=["base", "gone", "desktop"],
        )
        new = make_run(
            "new", {"base": [], "desktop": [], "server": []},
            seed_names=["base", "desktop", "server"],
        )
        diff = diff_runs(old, new)
        self.assertEqual(
            ["base", "desktop", "server", "gone"],
            [seed.name for seed in diff.seeds],
        )

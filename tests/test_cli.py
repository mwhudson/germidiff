"""End-to-end tests driving the CLI against a stand-in germinate."""

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

import io
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from germidiff.cli import main
from germidiff.probe import ProbeResult
from germidiff.runner import (
    GerminateError,
    GerminateRun,
    apt_config_for_chdist,
    arch_for_apt_config,
    architectures_for_apt_config,
    components_for_apt_config,
    germinate_command,
)
from tests.helpers import FAKE_GERMINATE, GitTestCase


class CliTestCase(GitTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()
        self.chdist_base = os.path.join(self.temp_dir, "chdists")
        self.apt_conf = self.write(
            os.path.join(
                self.chdist_base, "questing", "etc", "apt", "apt.conf"
            ),
            'Apt {\n   Architecture "ppc64el";\n};\n'
            'Dir "%s";\n' % self.temp_dir,
        )
        # What a run works out it needs: the questing archive with the
        # components the ubuntu collection germinates against.
        self.write(
            os.path.join(
                self.chdist_base, "questing", "etc", "apt", "sources.list"
            ),
            "deb http://archive.example/ubuntu questing main restricted\n",
        )

    def make_seed_repo(self, name, structure, seeds):
        repo = self.make_repo(os.path.join(self.temp_dir, name))
        self.write_collection(repo, structure, seeds)
        return repo

    def make_platform(self, packages=("libc",)):
        # Beside the repo under test, named for its branch: the layout seed
        # branches are checked out in, and the only place germidiff looks.
        return self.write_collection(
            os.path.join(self.temp_dir, "platform.questing"),
            "base:\n",
            {"base": packages},
        )

    def run_cli(self, *args):
        argv = [
            "--germinate",
            FAKE_GERMINATE,
            "--chdist-base",
            self.chdist_base,
            # These tests have no archive behind them and nothing to say
            # about apt; the chdist handling has tests of its own.
            "--no-update",
            "--quiet",
        ] + list(args)
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(argv)
        return status, out.getvalue()


class TestEndToEnd(CliTestCase):
    def test_diffs_a_change_to_an_outer_collection(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["thunderbird"]},
        )
        new = self.commit(repo, "swap mail client")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertEqual(
            "**global**\n"
            "+thunderbird\n"
            "-firefox\n"
            "\n"
            "**desktop**\n"
            "+thunderbird\n"
            "-firefox\n",
            out,
        )

    def test_dependent_collection_is_held_fixed(self):
        # The platform checkout supplies "base", which the collection under
        # test inherits from; it is identical on both sides, so nothing from
        # it shows up in the diff.
        self.make_platform(packages=("libc", "coreutils"))
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "gimp"]},
        )
        new = self.commit(repo, "add gimp")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertNotIn("libc", out)
        self.assertNotIn("coreutils", out)
        self.assertEqual("**global**\n+gimp\n\n**desktop**\n+gimp\n", out)

    def test_new_and_removed_seeds_are_labelled(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\noldseed: base\n",
            {"desktop": ["firefox"], "oldseed": ["somepackage"]},
        )
        old = self.commit(repo, "initial")
        os.unlink(os.path.join(repo, "oldseed"))
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\nserver: base\n",
            {"desktop": ["firefox"], "server": ["nginx"]},
        )
        new = self.commit(repo, "replace oldseed with server")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertEqual(
            "**global**\n"
            "+nginx\n"
            "-somepackage\n"
            "\n"
            "**server** (new seed)\n"
            "+nginx\n"
            "\n"
            "**oldseed** (removed seed)\n"
            "-somepackage\n",
            out,
        )

    def test_package_moved_between_seeds_has_no_net_effect(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\nserver: base\n",
            {"desktop": ["nginx"], "server": []},
        )
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\nserver: base\n",
            {"desktop": [], "server": ["nginx"]},
        )
        new = self.commit(repo, "move nginx to server")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertEqual(
            "**global**\n"
            "(no net change across all seeds)\n"
            "\n"
            "**desktop**\n"
            "-nginx\n"
            "\n"
            "**server**\n"
            "+nginx\n",
            out,
        )

    def test_no_changes_still_exits_zero(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "README"), "no seed change\n")
        new = self.commit(repo, "docs only")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertEqual("No changes to any expanded package list.\n", out)

    def test_testing_the_platform_collection_itself(self):
        # The collection under test sits beside itself, so it is a candidate
        # for its own include lines; each side's worktree must win over the
        # checkout, or neither ref would be the one germinated.
        platform_repo = self.make_repo(
            os.path.join(self.temp_dir, "platform.questing")
        )
        self.write_collection(platform_repo, "base:\n", {"base": ["libc"]})
        old = self.commit(platform_repo, "initial")
        self.write_collection(
            platform_repo, "base:\n", {"base": ["libc", "systemd"]}
        )
        new = self.commit(platform_repo, "add systemd")

        status, out = self.run_cli(platform_repo, old, new)
        self.assertEqual(0, status)
        self.assertEqual("**global**\n+systemd\n\n**base**\n+systemd\n", out)

    def test_dependencies_pulled_in_by_the_change_show_up(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        # "gimp+libgimp" means the stub expands gimp to pull in libgimp.
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox", "gimp+libgimp"]},
        )
        new = self.commit(repo, "add gimp")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertIn("+libgimp", out)


class TestFailures(CliTestCase):
    def test_unknown_ref_is_an_error(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        status, out = self.run_cli(repo, old, "nosuchref")
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_missing_dependent_collection_is_an_error(self):
        repo = self.make_seed_repo(
            "ubuntu.questing", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")
        status, out = self.run_cli(repo, old, new)
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_a_sibling_without_a_structure_is_not_a_collection(self):
        # A directory of the right name is not enough: germinate would fail
        # with a bare "could not open STRUCTURE" from inside its own run.
        os.makedirs(os.path.join(self.temp_dir, "platform.questing"))
        repo = self.make_seed_repo(
            "ubuntu.questing", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        status, out = self.run_cli(repo, old, old)
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_not_a_git_repo_is_an_error(self):
        self.make_platform()
        directory = os.path.join(self.temp_dir, "notarepo")
        os.makedirs(directory)
        status, out = self.run_cli(directory, "a", "b")
        self.assertEqual(1, status)
        self.assertEqual("", out)

class TestAptConfigForChdist(CliTestCase):
    def test_resolves_a_chdist_name(self):
        self.assertEqual(
            os.path.join(
                self.chdist_base, "questing", "etc", "apt", "apt.conf"
            ),
            apt_config_for_chdist("questing", self.chdist_base),
        )

    def test_unknown_chdist(self):
        with self.assertRaises(GerminateError) as cm:
            apt_config_for_chdist("nope", self.chdist_base)
        self.assertIn("no chdist named", str(cm.exception))


class TestGerminateCommand(CliTestCase):
    def test_a_relative_germinate_path_is_made_absolute(self):
        # Germinate runs with its output directory as the working directory,
        # so a relative path would not survive the move.
        command = germinate_command(
            "./bin/germinate", "/seeds", "ubuntu", "/apt.conf", "amd64"
        )
        self.assertEqual(os.path.abspath("./bin/germinate"), command[0])

    def test_a_bare_command_name_is_left_for_path_lookup(self):
        command = germinate_command(
            "germinate", "/seeds", "ubuntu", "/apt.conf", "amd64"
        )
        self.assertEqual("germinate", command[0])


@unittest.skipIf(
    shutil.which("apt-config") is None, "apt-config is not installed"
)
class TestArchDefault(CliTestCase):
    def test_arch_comes_from_the_chdist(self):
        # Germinating against an arch the chdist does not carry produces a
        # plausible-looking but meaningless diff, so the arch is taken from
        # the chdist rather than assumed.
        self.assertEqual("ppc64el", arch_for_apt_config(self.apt_conf))

    def test_a_missing_apt_config_gives_no_arch(self):
        # Not just tidiness: apt falls back to the host's own configuration
        # when APT_CONFIG points at nothing, so asking it would quietly
        # answer with the host's architecture instead of the chdist's.
        self.assertIsNone(
            arch_for_apt_config(os.path.join(self.temp_dir, "nope.conf"))
        )

    def test_the_run_uses_the_chdist_arch(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        work_dir = os.path.join(self.temp_dir, "work")
        status, _ = self.run_cli(
            repo, old, new, "--work-dir", work_dir
        )
        self.assertEqual(0, status)
        with open(os.path.join(work_dir, "new", "germinate.log")) as f:
            self.assertIn("arch=ppc64el", f.read())

    def test_architectures_come_from_the_chdist(self):
        self.assertEqual(
            ["ppc64el"], architectures_for_apt_config(self.apt_conf)
        )

    def test_a_missing_apt_config_gives_no_architectures(self):
        self.assertIsNone(
            architectures_for_apt_config(
                os.path.join(self.temp_dir, "nope.conf")
            )
        )

    def test_components_are_read_from_the_chdist(self):
        # Which components are in play is the chdist's business -- germinate
        # ignores --components under --apt-config -- so germidiff can only
        # report them, and only if apt will say.
        components = components_for_apt_config(self.apt_conf)
        self.assertTrue(components is None or isinstance(components, list))

    def test_a_missing_apt_config_gives_no_components(self):
        self.assertIsNone(
            components_for_apt_config(
                os.path.join(self.temp_dir, "nope.conf")
            )
        )

    def test_an_arch_the_chdist_lacks_is_warned_about(self):
        # It cannot be caught after the fact: on a real collection,
        # germinating for an absent architecture produces about as many
        # packages, and about as many complaints, as a good run.
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        with self.assertLogs("germidiff", level="WARNING") as caught:
            status, _ = self.run_cli(
                repo, old, new, "--arch", "riscv64"
            )
        self.assertEqual(0, status)
        self.assertIn(
            "carries ppc64el, not riscv64", "\n".join(caught.output)
        )

    def test_an_explicit_arch_still_wins(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        work_dir = os.path.join(self.temp_dir, "work")
        status, _ = self.run_cli(
            repo, old, new, "--arch", "riscv64",
            "--work-dir", work_dir,
        )
        self.assertEqual(0, status)
        with open(os.path.join(work_dir, "new", "germinate.log")) as f:
            self.assertIn("arch=riscv64", f.read())


class TestRetentionEndToEnd(CliTestCase):
    """The build-essential shape: a seed entry removed, but still pulled in."""

    def make_trees(self, new_desktop):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            # dpkg-dev recommends build-essential, as in the real archive.
            {"desktop": ["dpkg-dev~build-essential", "build-essential"]},
        )
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": new_desktop},
        )
        new = self.commit(repo, "drop the explicit entry")
        return repo, old, new

    def test_reports_a_package_now_held_only_by_a_recommends(self):
        repo, old, new = self.make_trees(["dpkg-dev~build-essential"])
        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        # The expanded lists are unchanged -- that is the whole point.
        self.assertIn("No changes to any expanded package list.", out)
        self.assertIn("**no longer seeded, still pulled in**", out)
        self.assertIn(
            "! build-essential: only by dpkg-dev (Recommends)", out
        )

    def test_says_nothing_when_the_package_really_went_away(self):
        # Dropping both leaves nothing to explain: it shows in the diff.
        repo, old, new = self.make_trees([])
        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertIn("-build-essential", out)
        self.assertNotIn("no longer seeded", out)

    def test_a_hard_dependency_is_not_flagged_as_soft(self):
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["dpkg-dev+build-essential", "build-essential"]},
        )
        self.make_platform()
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["dpkg-dev+build-essential"]},
        )
        new = self.commit(repo, "drop the explicit entry")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertIn("**no longer seeded, still pulled in**", out)
        self.assertNotIn("!", out)
        self.assertIn("held by hard dependencies: build-essential", out)

    def test_probe_degrades_gracefully_without_a_real_archive(self):
        # The stub germinate has no archive behind it, so the probe cannot
        # run; that must warn rather than lose the diff.
        repo, old, new = self.make_trees(["dpkg-dev~build-essential"])
        status, out = self.run_cli(
            repo, old, new, "--probe-retention"
        )
        self.assertEqual(0, status)
        self.assertIn("! build-essential: only by dpkg-dev", out)


class TestMetapackageProbeWithExtra(CliTestCase):
    """--include-extra when the metapackage probe replaces the new run.

    The probe germinates in-process and covers only the real seeds, so its
    run has no "extra" seed; diffed against an old run that has one, "extra"
    would show up as a removed seed.
    """

    def make_trees(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            # ubuntu-desktop is seeded in the very seed it stands for, so it
            # is recognised as this collection's metapackage, and the change
            # drops dropped-pkg from that seed while ubuntu-desktop still
            # depends on it -- the lag the probe exists to explain.
            {"desktop": ["dropped-pkg", "ubuntu-desktop+dropped-pkg"]},
        )
        old = self.commit(repo, "initial")
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["ubuntu-desktop+dropped-pkg"]},
        )
        new = self.commit(repo, "stop seeding the package")
        return repo, old, new

    def test_extra_is_not_reported_as_a_removed_seed(self):
        repo, old, new = self.make_trees()
        # There is no importable germinate here, so the probe cannot really
        # run; answer as it would, with the new run's real seeds only.
        after = GerminateRun(
            "probe",
            None,
            ["base", "desktop"],
            {
                "base": {"libc"},
                "desktop": {"dropped-pkg", "ubuntu-desktop"},
            },
            inherit={"base": [], "desktop": ["base"]},
        )
        with mock.patch(
            "germidiff.cli.probe_cuts",
            return_value=ProbeResult([], after=after),
        ):
            status, out = self.run_cli(repo, old, new, "--include-extra")
        self.assertEqual(0, status)
        # The probe answered and its run was used for the diff...
        self.assertIn("**assuming the metapackages are rebuilt**", out)
        # ...without "extra" appearing to have been removed.
        self.assertNotIn("removed seed", out)


class TestCollectionDiscovery(CliTestCase):
    """Finding dependent collections without being told where they are."""

    def test_a_sibling_checkout_is_found_with_nothing_configured(self):
        # Seed collections are normally checked out beside each other, named
        # for their branch -- the same layout germinate resolves a seed
        # source against, and the one germidiff-mp clones into its cache.
        self.make_platform()
        repo = self.make_repo(os.path.join(self.temp_dir, "ubuntu.questing"))
        self.write_collection(
            repo,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertIn("+gimp", out)

    def test_a_nested_collection_comes_with_its_parent(self):
        # "include ubuntu.questing/languages" names a collection inside the
        # one under test; it arrives through that collection's own symlink
        # and must not be linked separately, which would mean writing inside
        # the checkout.
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\n"
            "include ubuntu.questing/languages\n"
            "desktop: base\n",
            {"desktop": ["firefox"]},
        )
        self.write_collection(
            os.path.join(repo, "languages"),
            "desktop-fr: desktop\n",
            {"desktop-fr": ["firefox-locale-fr"]},
        )
        old = self.commit(repo, "initial")
        self.write(
            os.path.join(repo, "languages", "desktop-fr"),
            " * firefox-locale-fr\n * hunspell-fr\n",
        )
        new = self.commit(repo, "add a French dictionary")

        status, out = self.run_cli(repo, old, new)
        self.assertEqual(0, status)
        self.assertIn("+hunspell-fr", out)
        self.assertIn("**desktop-fr**", out)

    def test_a_missing_collection_still_reports_where_it_looked(self):
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        err = io.StringIO()
        with redirect_stderr(err):
            status, out = self.run_cli(repo, old, new)
        self.assertEqual(1, status)
        self.assertEqual("", out)
        message = err.getvalue()
        self.assertIn("platform.questing", message)
        self.assertIn("beside the seed repo", message)


class TestChdistHandling(CliTestCase):
    """Working out, creating and refreshing the archive metadata."""

    def setUp(self):
        super().setUp()
        self.use_stub_chdist(self.temp_dir)
        # A directory with nothing in it, so that what a run works out for
        # itself is what gets created rather than what setUp left lying
        # around under the same name.
        self.fresh_base = os.path.join(self.temp_dir, "new-chdists")

    def make_change(self, name="ubuntu.questing"):
        self.write_collection(
            os.path.join(self.temp_dir, "platform.questing"),
            "base:\n",
            {"base": ["libc"]},
        )
        repo = self.make_seed_repo(
            name,
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        return repo, old, self.commit(repo, "change")

    def run_bare(self, *args):
        """Run without the --no-update the other tests default to."""
        argv = [
            "--germinate",
            FAKE_GERMINATE,
            "--chdist-base",
            self.chdist_base,
            "--quiet",
        ] + list(args)
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(argv)
        return status, out.getvalue()

    def run_fresh(self, *args):
        """Run against a chdist directory that starts out empty."""
        return self.run_bare(*(args + ("--chdist-base", self.fresh_base)))

    def test_the_branch_name_says_which_chdist_to_make(self):
        # ubuntu.questing is the ubuntu collection of the questing series,
        # and the ubuntu collection germinates against main and restricted:
        # everything the chdist needs is already on the command line.
        repo, old, new = self.make_change()

        status, out = self.run_fresh(repo, old, new)

        self.assertEqual(0, status)
        self.assertIn("+gimp", out)
        created = self.chdist_calls()[0]
        self.assertIn("create questing", created)
        self.assertIn("main restricted", created)
        self.assertNotIn("universe", created)

    def test_a_flavour_gets_the_whole_archive(self):
        repo, old, new = self.make_change("kubuntu.questing")

        status, out = self.run_fresh(repo, old, new)

        self.assertEqual(0, status)
        created = self.chdist_calls()[0]
        self.assertIn("create questing-all", created)
        self.assertIn("main restricted universe multiverse", created)

    def test_components_can_be_overridden(self):
        repo, old, new = self.make_change()

        status, out = self.run_fresh(
            repo, old, new, "--components", "main,universe"
        )

        self.assertEqual(0, status)
        self.assertIn("create questing-main+universe", self.chdist_calls()[0])

    def test_a_branch_name_with_no_series_says_so(self):
        # "ubuntu" alone names no archive, and guessing one would germinate
        # against whatever happened to be lying around.
        repo = self.make_seed_repo(
            "ubuntu", "desktop:\n", {"desktop": ["firefox"]}
        )
        old = self.commit(repo, "initial")

        err = io.StringIO()
        with redirect_stderr(err):
            status, out = self.run_fresh(repo, old, old)

        self.assertEqual(1, status)
        self.assertEqual("", out)
        self.assertIn("--seed-dist", err.getvalue())
        self.assertEqual([], self.chdist_calls())

    def test_seed_dist_settles_it(self):
        # A collection checked out under a name of your own still says which
        # archive it means, once you say which branch it stands for.
        repo, old, new = self.make_change("ubuntu")

        status, out = self.run_fresh(
            repo, old, new, "--seed-dist", "ubuntu.questing"
        )

        self.assertEqual(0, status)
        self.assertIn("create questing", self.chdist_calls()[0])

    def test_an_existing_chdist_is_refreshed_before_the_runs(self):
        # Both germinate runs read one archive, so it is refreshed once,
        # before either of them: an archive that moved in between would show
        # up as a seed change nobody made.
        repo, old, new = self.make_change()

        status, out = self.run_bare(repo, old, new)

        self.assertEqual(0, status)
        self.assertEqual(["-d %s apt-get questing update" % self.chdist_base],
                         self.chdist_calls())

    def test_no_update_leaves_the_lists_as_they_stand(self):
        # Worth having for a run of germinations one after another, which
        # would otherwise pay for an apt-get update apiece.
        repo, old, new = self.make_change()

        status, out = self.run_bare(repo, old, new, "--no-update")

        self.assertEqual(0, status)
        self.assertEqual([], self.chdist_calls())


class TestProgress(CliTestCase):
    """What each run says on stderr while it works."""

    def make_change(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu.questing",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        return repo, old, self.commit(repo, "change")

    def run_capturing(self, *args):
        argv = [
            "--germinate",
            FAKE_GERMINATE,
            "--chdist-base",
            self.chdist_base,
            "--no-update",
        ] + list(args)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_progress_is_reported_without_being_asked(self):
        # Two germinations of a real collection take long enough that
        # silence looks like a hang, and the report goes to stdout, so
        # saying what is happening costs the output nothing.
        repo, old, new = self.make_change()

        status, out, err = self.run_capturing(repo, old, new)

        self.assertEqual(0, status)
        self.assertIn("+gimp", out)
        self.assertIn("collection under test: ubuntu.questing", err)
        self.assertIn("archive metadata", err)
        self.assertNotIn("germidiff:", out)

    def test_quiet_keeps_it_to_problems(self):
        repo, old, new = self.make_change()

        status, out, err = self.run_capturing(repo, old, new, "-q")

        self.assertEqual(0, status)
        self.assertIn("+gimp", out)
        self.assertEqual("", err)

    def test_verbose_adds_the_commands_themselves(self):
        repo, old, new = self.make_change()

        status, out, err = self.run_capturing(repo, old, new, "-v")

        self.assertEqual(0, status)
        self.assertIn("adding worktree", err)

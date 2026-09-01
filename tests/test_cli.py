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
from contextlib import redirect_stdout

from germidiff.cli import main
from germidiff.runner import (
    GerminateError,
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

    def make_seed_repo(self, name, structure, seeds):
        repo = self.make_repo(os.path.join(self.temp_dir, name))
        self.write_collection(repo, structure, seeds)
        return repo

    def make_platform(self, packages=("libc",)):
        platform = self.write_collection(
            os.path.join(self.temp_dir, "platform"),
            "base:\n",
            {"base": packages},
        )
        self.write(
            os.path.join(self.temp_dir, "collections.conf"),
            "[collections]\nplatform.questing = %s\n" % platform,
        )
        return platform

    def run_cli(self, *args):
        argv = [
            "--germinate",
            FAKE_GERMINATE,
            "--chdist-base",
            self.chdist_base,
            "--collection-map",
            os.path.join(self.temp_dir, "collections.conf"),
        ] + list(args)
        out = io.StringIO()
        with redirect_stdout(out):
            status = main(argv)
        return status, out.getvalue()


class TestEndToEnd(CliTestCase):
    def test_diffs_a_change_to_an_outer_collection(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
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
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertNotIn("libc", out)
        self.assertNotIn("coreutils", out)
        self.assertEqual("**global**\n+gimp\n\n**desktop**\n+gimp\n", out)

    def test_new_and_removed_seeds_are_labelled(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
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
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
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
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "README"), "no seed change\n")
        new = self.commit(repo, "docs only")

        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertEqual("No changes to any expanded package list.\n", out)

    def test_testing_the_platform_collection_itself(self):
        # The collection under test is the one the map calls
        # platform.questing; its own worktrees must win over the map entry.
        platform_repo = self.make_repo(os.path.join(self.temp_dir, "platform"))
        self.write_collection(platform_repo, "base:\n", {"base": ["libc"]})
        old = self.commit(platform_repo, "initial")
        self.write_collection(
            platform_repo, "base:\n", {"base": ["libc", "systemd"]}
        )
        new = self.commit(platform_repo, "add systemd")
        self.write(
            os.path.join(self.temp_dir, "collections.conf"),
            "[collections]\nplatform.questing = %s\n" % platform_repo,
        )

        status, out = self.run_cli(platform_repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertEqual("**global**\n+systemd\n\n**base**\n+systemd\n", out)

    def test_dependencies_pulled_in_by_the_change_show_up(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertIn("+libgimp", out)


class TestFailures(CliTestCase):
    def test_unknown_ref_is_an_error(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        status, out = self.run_cli(repo, old, "nosuchref", "questing")
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_missing_dependent_collection_is_an_error(self):
        self.write(
            os.path.join(self.temp_dir, "collections.conf"),
            "[collections]\n",
        )
        repo = self.make_seed_repo(
            "ubuntu", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")
        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_broken_collection_map_entry_is_an_error(self):
        self.write(
            os.path.join(self.temp_dir, "collections.conf"),
            "[collections]\nplatform.questing = %s\n"
            % os.path.join(self.temp_dir, "nowhere"),
        )
        repo = self.make_seed_repo(
            "ubuntu", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        status, out = self.run_cli(repo, old, old, "questing")
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_not_a_git_repo_is_an_error(self):
        self.make_platform()
        directory = os.path.join(self.temp_dir, "notarepo")
        os.makedirs(directory)
        status, out = self.run_cli(directory, "a", "b", "questing")
        self.assertEqual(1, status)
        self.assertEqual("", out)

    def test_unknown_chdist_is_an_error(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu", "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        status, out = self.run_cli(repo, old, old, "nosuchchdist")
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

    def test_accepts_a_chdist_directory(self):
        directory = os.path.join(self.chdist_base, "questing")
        self.assertEqual(
            os.path.join(directory, "etc", "apt", "apt.conf"),
            apt_config_for_chdist(directory, self.chdist_base),
        )

    def test_accepts_an_apt_conf_path(self):
        self.assertEqual(
            self.apt_conf,
            apt_config_for_chdist(self.apt_conf, self.chdist_base),
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
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        work_dir = os.path.join(self.temp_dir, "work")
        status, _ = self.run_cli(
            repo, old, new, "questing", "--work-dir", work_dir
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
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        with self.assertLogs("germidiff", level="WARNING") as caught:
            status, _ = self.run_cli(
                repo, old, new, "questing", "--arch", "riscv64"
            )
        self.assertEqual(0, status)
        self.assertIn(
            "carries ppc64el, not riscv64", "\n".join(caught.output)
        )

    def test_an_explicit_arch_still_wins(self):
        self.make_platform()
        repo = self.make_seed_repo(
            "ubuntu",
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        old = self.commit(repo, "initial")
        self.write(os.path.join(repo, "desktop"), " * gimp\n")
        new = self.commit(repo, "change")

        work_dir = os.path.join(self.temp_dir, "work")
        status, _ = self.run_cli(
            repo, old, new, "questing", "--arch", "riscv64",
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
            "ubuntu",
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
        status, out = self.run_cli(repo, old, new, "questing")
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
        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertIn("-build-essential", out)
        self.assertNotIn("no longer seeded", out)

    def test_a_hard_dependency_is_not_flagged_as_soft(self):
        repo = self.make_seed_repo(
            "ubuntu",
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

        status, out = self.run_cli(repo, old, new, "questing")
        self.assertEqual(0, status)
        self.assertIn("**no longer seeded, still pulled in**", out)
        self.assertNotIn("!", out)
        self.assertIn("held by hard dependencies: build-essential", out)

    def test_probe_degrades_gracefully_without_a_real_archive(self):
        # The stub germinate has no archive behind it, so the probe cannot
        # run; that must warn rather than lose the diff.
        repo, old, new = self.make_trees(["dpkg-dev~build-essential"])
        status, out = self.run_cli(
            repo, old, new, "questing", "--probe-retention"
        )
        self.assertEqual(0, status)
        self.assertIn("! build-essential: only by dpkg-dev", out)

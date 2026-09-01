"""End-to-end tests driving the CLI against a stand-in germinate."""

import io
import os
from contextlib import redirect_stdout

from germinate_diff.cli import main
from germinate_diff.runner import (
    GerminateError,
    apt_config_for_chdist,
    germinate_command,
)
from tests.helpers import FAKE_GERMINATE, GitTestCase


class CliTestCase(GitTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()
        self.chdist_base = os.path.join(self.temp_dir, "chdists")
        self.write(
            os.path.join(self.chdist_base, "questing", "etc", "apt",
                         "apt.conf"),
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
        path = os.path.join(
            self.chdist_base, "questing", "etc", "apt", "apt.conf"
        )
        self.assertEqual(path, apt_config_for_chdist(path, self.chdist_base))

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

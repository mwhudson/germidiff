"""Tests against germinate itself, skipped when it isn't importable.

These check the two assumptions germidiff makes about germinate that the
stand-in in ``fake_germinate.py`` cannot: that the command line we build is
the one germinate's own option parser accepts, and that germinate really does
resolve a collection and everything it includes out of the symlink farm we
build for it.
"""

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
import unittest

from germidiff.runner import build_seed_base, germinate_command
from tests.helpers import TestCase

try:
    from germinate.scripts.germinate_main import parse_options
    from germinate.seeds import SeedStructure
except ImportError:  # pragma: no cover - depends on the host
    parse_options = None
    SeedStructure = None


needs_germinate = unittest.skipIf(
    parse_options is None, "germinate is not installed"
)


@needs_germinate
class TestCommandLine(TestCase):
    def parse(self, extra_args=()):
        command = germinate_command(
            "germinate",
            "/seeds",
            "ubuntu.questing",
            "/chdist/etc/apt/apt.conf",
            "amd64",
            extra_args,
        )
        return parse_options(command)

    def test_germinate_accepts_our_options(self):
        options = self.parse()
        self.assertEqual(["/seeds"], options.seeds)
        self.assertEqual("ubuntu.questing", options.release)
        self.assertEqual("/chdist/etc/apt/apt.conf", options.apt_config)
        self.assertEqual("amd64", options.arch)

    def test_seeds_are_read_from_the_filesystem_not_a_vcs(self):
        # The symlink farm only works because germinate treats a seed source
        # with no --vcs option as a plain directory.
        self.assertIsNone(self.parse().vcs)

    def test_no_rdepends_is_understood(self):
        self.assertFalse(self.parse(["--no-rdepends"]).want_rdepends)
        self.assertTrue(self.parse().want_rdepends)


@needs_germinate
class TestSeedResolution(TestCase):
    def test_germinate_resolves_includes_through_the_seed_base(self):
        temp_dir = self.make_temp_dir()

        under_test = self.write_collection(
            os.path.join(temp_dir, "checkout"),
            "include platform.questing\ndesktop: base\n",
            {"desktop": ["firefox"]},
        )
        platform = self.write_collection(
            os.path.join(temp_dir, "platform"),
            "base:\n",
            {"base": ["libc"]},
        )

        seed_base = build_seed_base(
            os.path.join(temp_dir, "seeds"),
            "ubuntu.questing",
            under_test,
            {"platform.questing": platform},
        )

        structure = SeedStructure(
            "ubuntu.questing", seed_bases=[seed_base], vcs=None
        )
        # Both the collection under test and the included one resolved, with
        # inheritance expanded across the two.
        self.assertEqual(["base", "desktop"], sorted(structure.names))
        self.assertEqual(["base"], structure.inner_seeds("desktop")[:-1])
        self.assertIn("firefox", structure["desktop"].text)
        self.assertIn("libc", structure["base"].text)

    def test_the_collection_under_test_wins_over_the_map(self):
        # Testing the platform collection itself: the map still lists it, but
        # the worktree is what germinate must read.
        temp_dir = self.make_temp_dir()
        under_test = self.write_collection(
            os.path.join(temp_dir, "checkout"), "base:\n", {"base": ["new"]}
        )
        fixed = self.write_collection(
            os.path.join(temp_dir, "fixed"), "base:\n", {"base": ["old"]}
        )

        seed_base = build_seed_base(
            os.path.join(temp_dir, "seeds"),
            "platform.questing",
            under_test,
            {"platform.questing": fixed},
        )

        structure = SeedStructure(
            "platform.questing", seed_bases=[seed_base], vcs=None
        )
        self.assertIn("new", structure["base"].text)
        self.assertNotIn("old", structure["base"].text)

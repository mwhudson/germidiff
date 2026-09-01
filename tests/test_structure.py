"""Tests for STRUCTURE parsing."""

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

from germidiff.structure import (
    StructureError,
    parse_structure,
    required_branches,
    seed_names_from_structure_output,
)
from tests.helpers import TestCase


class TestParseStructure(TestCase):
    def test_reads_seeds_and_includes(self):
        path = self.write(
            os.path.join(self.make_temp_dir(), "STRUCTURE"),
            "# a comment\n"
            "\n"
            "include platform.questing\n"
            "feature follow-recommends\n"
            "base:\n"
            "desktop: base\n"
            "supported: base desktop\n",
        )
        seed_order, includes = parse_structure(path)
        self.assertEqual(["base", "desktop", "supported"], seed_order)
        self.assertEqual(["platform.questing"], includes)

    def test_multiple_branches_on_one_include_line(self):
        path = self.write(
            os.path.join(self.make_temp_dir(), "STRUCTURE"),
            "include one.dist two.dist\nbase:\n",
        )
        _, includes = parse_structure(path)
        self.assertEqual(["one.dist", "two.dist"], includes)

    def test_missing_file(self):
        path = os.path.join(self.make_temp_dir(), "STRUCTURE")
        self.assertRaises(StructureError, parse_structure, path)

    def test_seed_names_from_structure_output(self):
        path = self.write(
            os.path.join(self.make_temp_dir(), "structure"),
            "base:\ndesktop: base\n",
        )
        self.assertEqual(
            ["base", "desktop"], seed_names_from_structure_output(path)
        )


class TestRequiredBranches(TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()
        self.dirs = {}

    def add(self, branch, structure):
        directory = os.path.join(self.temp_dir, branch)
        self.write(os.path.join(directory, "STRUCTURE"), structure)
        self.dirs[branch] = directory
        return directory

    def resolve(self, branch):
        return self.dirs.get(branch)

    def test_no_includes(self):
        self.add("ubuntu", "base:\n")
        order, missing = required_branches("ubuntu", self.resolve)
        self.assertEqual(["ubuntu"], order)
        self.assertEqual([], missing)

    def test_follows_includes_transitively(self):
        self.add("desktop", "include platform.questing\ndesktop: base\n")
        self.add("platform.questing", "include core.questing\nbase:\n")
        self.add("core.questing", "core:\n")
        order, missing = required_branches("desktop", self.resolve)
        self.assertEqual(
            ["desktop", "platform.questing", "core.questing"], order
        )
        self.assertEqual([], missing)

    def test_reports_missing_branch_and_who_included_it(self):
        self.add("desktop", "include platform.questing\ndesktop:\n")
        order, missing = required_branches("desktop", self.resolve)
        self.assertEqual(["desktop"], order)
        self.assertEqual([("platform.questing", "desktop")], missing)

    def test_survives_an_include_cycle(self):
        self.add("one", "include two\nbase:\n")
        self.add("two", "include one\nother:\n")
        order, missing = required_branches("one", self.resolve)
        self.assertEqual(["one", "two"], order)
        self.assertEqual([], missing)

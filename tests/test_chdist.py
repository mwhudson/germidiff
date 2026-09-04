"""Tests for finding and creating the chdist a run reads the archive from."""

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

from germidiff.chdist import (
    ALL_COMPONENTS,
    ChdistError,
    MAIN_COMPONENTS,
    _sources_components,
    chdist_base,
    chdist_name,
    components_for_collection,
    ensure_chdist,
)
from tests.helpers import TestCase

# A stub standing in for devscripts' chdist, recording how it was called and
# writing just enough of a chdist for the checks to have something to read.
STUB_CHDIST = """\
#! /usr/bin/env python3
import os
import sys

args = sys.argv[1:]
with open(os.environ["CHDIST_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")

base = args[args.index("-d") + 1]
if not os.path.isdir(base):
    # The real chdist resolves its data directory with abs_path(), which
    # gives up on one that does not exist and leaves it working from an
    # undefined path.
    sys.stderr.write("can't open dir %s\\n" % base)
    sys.exit(1)
if "create" in args:
    rest = args[args.index("create") + 1:]
    name, mirror, series, components = rest[0], rest[1], rest[2], rest[3:]
    directory = os.path.join(base, name, "etc", "apt")
    os.makedirs(directory)
    with open(os.path.join(directory, "sources.list"), "w") as f:
        f.write("deb %s %s %s\\n" % (mirror, series, " ".join(components)))
"""


class TestComponentsForCollection(TestCase):
    def test_ubuntu_and_platform_get_main_and_restricted(self):
        # These seeds are a statement about what Ubuntu supports, so they may
        # only resolve against the components Ubuntu supports.
        self.assertEqual(MAIN_COMPONENTS, components_for_collection("ubuntu"))
        self.assertEqual(
            MAIN_COMPONENTS, components_for_collection("platform")
        )

    def test_flavours_get_the_whole_archive(self):
        self.assertEqual(ALL_COMPONENTS, components_for_collection("kubuntu"))

    def test_overrides_win(self):
        self.assertEqual(
            ("main",),
            components_for_collection("kubuntu", {"kubuntu": ["main"]}),
        )


class TestChdistName(TestCase):
    def test_main_and_restricted_is_the_bare_series(self):
        self.assertEqual("resolute", chdist_name("resolute", MAIN_COMPONENTS))

    def test_everything_is_suffixed(self):
        self.assertEqual(
            "resolute-all", chdist_name("resolute", ALL_COMPONENTS)
        )

    def test_anything_else_spells_itself_out(self):
        self.assertEqual(
            "resolute-main+universe",
            chdist_name("resolute", ("main", "universe")),
        )


class TestChdistBase(TestCase):
    def test_prefers_the_argument(self):
        self.assertEqual("/somewhere", chdist_base("/somewhere"))

    def test_defaults_to_a_directory_of_its_own(self):
        os.environ["XDG_CACHE_HOME"] = "/cache"
        self.addCleanup(os.environ.pop, "XDG_CACHE_HOME", None)
        self.assertEqual("/cache/germidiff/chdists", chdist_base())

    def test_ignores_chdist_home(self):
        # The whole point of keeping our own: creating and updating chdists
        # named after the series must not touch the ones you made by hand.
        os.environ["CHDIST_HOME"] = "/from/env"
        self.addCleanup(os.environ.pop, "CHDIST_HOME", None)
        os.environ["XDG_CACHE_HOME"] = "/cache"
        self.addCleanup(os.environ.pop, "XDG_CACHE_HOME", None)
        self.assertEqual("/cache/germidiff/chdists", chdist_base())


class TestSourcesComponents(TestCase):
    def parse(self, text, series="resolute"):
        path = self.write(
            os.path.join(self.make_temp_dir(), "sources.list"), text
        )
        return _sources_components(path, series)

    def test_reads_components_for_the_series(self):
        self.assertEqual(
            {"main", "restricted"},
            self.parse(
                "deb http://archive.ubuntu.com/ubuntu resolute main"
                " restricted\n"
                "deb-src http://archive.ubuntu.com/ubuntu resolute main"
                " restricted\n"
            ),
        )

    def test_unions_across_lines(self):
        self.assertEqual(
            {"main", "universe"},
            self.parse(
                "deb http://archive.ubuntu.com/ubuntu resolute main\n"
                "deb http://archive.ubuntu.com/ubuntu resolute universe\n"
            ),
        )

    def test_ignores_other_series(self):
        self.assertEqual(
            {"main"},
            self.parse(
                "deb http://archive.ubuntu.com/ubuntu resolute main\n"
                "deb http://archive.ubuntu.com/ubuntu questing universe\n"
            ),
        )

    def test_ignores_comments(self):
        self.assertEqual(
            {"main"},
            self.parse(
                "# deb http://archive.ubuntu.com/ubuntu resolute universe\n"
                "deb http://archive.ubuntu.com/ubuntu resolute main\n"
            ),
        )

    def test_skips_bracketed_options(self):
        self.assertEqual(
            {"main"},
            self.parse(
                "deb [arch=amd64 trusted=yes] http://archive.ubuntu.com/ubuntu"
                " resolute main\n"
            ),
        )

    def test_nothing_for_that_series_is_not_the_same_as_none(self):
        self.assertIsNone(
            self.parse("deb http://archive.ubuntu.com/ubuntu questing main\n")
        )


class TestEnsureChdist(TestCase):
    def setUp(self):
        self.temp_dir = self.make_temp_dir()
        self.base = os.path.join(self.temp_dir, "chdists")
        os.makedirs(self.base)
        self.log = os.path.join(self.temp_dir, "calls")
        stub_dir = os.path.join(self.temp_dir, "bin")
        path = self.write(os.path.join(stub_dir, "chdist"), STUB_CHDIST)
        os.chmod(path, 0o755)
        os.environ["PATH"] = stub_dir + os.pathsep + os.environ["PATH"]
        os.environ["CHDIST_LOG"] = self.log
        self.addCleanup(os.environ.pop, "CHDIST_LOG", None)
        self.original_path = os.environ["PATH"]
        self.addCleanup(self._restore_path, os.environ["PATH"])

    def _restore_path(self, path):
        os.environ["PATH"] = path.split(os.pathsep, 1)[1]

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.strip() for line in f if line.strip()]

    def existing(self, name, text):
        self.write(
            os.path.join(self.base, name, "etc", "apt", "sources.list"), text
        )

    def test_creates_and_updates_a_missing_chdist(self):
        name = ensure_chdist(
            "resolute", MAIN_COMPONENTS, "amd64", base=self.base
        )

        self.assertEqual("resolute", name)
        calls = self.calls()
        self.assertIn("create resolute", " ".join(calls))
        self.assertIn("main restricted", calls[0])
        self.assertIn("apt-get resolute update", calls[1])

    def test_creates_the_base_directory_it_was_pointed_at(self):
        # germidiff's own chdist directory does not exist until the first
        # run, and chdist itself cannot resolve a data directory that is not
        # there yet.
        base = os.path.join(self.temp_dir, "fresh", "chdists")

        ensure_chdist("resolute", MAIN_COMPONENTS, "amd64", base=base)

        self.assertIn("-d %s" % base, self.calls()[0])
        self.assertTrue(os.path.isdir(base))

    def test_a_created_chdist_is_updated_even_when_asked_not_to(self):
        # It has no package lists at all, so there is nothing to reuse.
        ensure_chdist(
            "resolute",
            MAIN_COMPONENTS,
            "amd64",
            base=self.base,
            update=False,
        )
        self.assertIn("apt-get resolute update", self.calls()[1])

    def test_reuses_a_matching_chdist(self):
        self.existing(
            "resolute",
            "deb http://archive.ubuntu.com/ubuntu resolute main restricted\n",
        )

        ensure_chdist("resolute", MAIN_COMPONENTS, "amd64", base=self.base)

        self.assertEqual(1, len(self.calls()))
        self.assertIn("apt-get resolute update", self.calls()[0])

    def test_no_update_leaves_an_existing_chdist_alone(self):
        self.existing(
            "resolute",
            "deb http://archive.ubuntu.com/ubuntu resolute main restricted\n",
        )

        ensure_chdist(
            "resolute",
            MAIN_COMPONENTS,
            "amd64",
            base=self.base,
            update=False,
        )

        self.assertEqual([], self.calls())

    def test_refuses_a_chdist_with_the_wrong_components(self):
        # Germinate takes its components from the chdist alone, so using this
        # one would resolve the platform seeds against universe and say so
        # nowhere.
        self.existing(
            "resolute",
            "deb http://archive.ubuntu.com/ubuntu resolute main restricted"
            " universe multiverse\n",
        )

        with self.assertRaises(ChdistError) as raised:
            ensure_chdist("resolute", MAIN_COMPONENTS, "amd64", base=self.base)

        message = str(raised.exception)
        self.assertIn("universe", message)
        self.assertIn("--no-check-components", message)
        self.assertEqual([], self.calls())

    def test_wrong_components_can_be_overridden(self):
        self.existing(
            "resolute",
            "deb http://archive.ubuntu.com/ubuntu resolute main restricted"
            " universe multiverse\n",
        )

        ensure_chdist(
            "resolute",
            MAIN_COMPONENTS,
            "amd64",
            base=self.base,
            check_components=False,
        )

        self.assertIn("apt-get resolute update", self.calls()[0])

    def test_refuses_a_chdist_that_lacks_the_series(self):
        self.existing(
            "resolute",
            "deb http://archive.ubuntu.com/ubuntu questing main restricted\n",
        )

        with self.assertRaises(ChdistError) as raised:
            ensure_chdist("resolute", MAIN_COMPONENTS, "amd64", base=self.base)
        self.assertIn("does not carry resolute", str(raised.exception))

    def test_refuses_a_directory_that_is_not_a_chdist(self):
        os.makedirs(os.path.join(self.base, "resolute"))

        with self.assertRaises(ChdistError) as raised:
            ensure_chdist("resolute", MAIN_COMPONENTS, "amd64", base=self.base)
        self.assertIn("not a chdist", str(raised.exception))

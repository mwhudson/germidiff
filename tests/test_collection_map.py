"""Tests for the collection map."""

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

from germidiff.collection_map import (
    CollectionMapError,
    load_collection_map,
    parse_overrides,
    validate_entry,
)
from tests.helpers import TestCase


class TestCollectionMap(TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()

    def make_checkout(self, name):
        directory = os.path.join(self.temp_dir, name)
        self.write(os.path.join(directory, "STRUCTURE"), "base:\n")
        return directory

    def make_config(self, text):
        return self.write(
            os.path.join(self.temp_dir, "collections.conf"), text
        )

    def test_reads_entries(self):
        platform = self.make_checkout("platform")
        config = self.make_config(
            "[collections]\nplatform.questing = %s\n" % platform
        )
        collection_map = load_collection_map(config_path=config)
        self.assertEqual(platform, collection_map.get("platform.questing"))
        self.assertIn("platform.questing", collection_map)

    def test_keys_keep_their_case(self):
        checkout = self.make_checkout("Platform")
        config = self.make_config(
            "[collections]\nPlatform.Questing = %s\n" % checkout
        )
        collection_map = load_collection_map(config_path=config)
        self.assertEqual(["Platform.Questing"], list(collection_map))

    def test_relative_paths_are_relative_to_the_config_file(self):
        self.make_checkout("platform")
        config = self.make_config("[collections]\nplatform = platform\n")
        collection_map = load_collection_map(config_path=config)
        self.assertEqual(
            os.path.join(self.temp_dir, "platform"),
            collection_map.get("platform"),
        )

    def test_overrides_win(self):
        first = self.make_checkout("first")
        second = self.make_checkout("second")
        config = self.make_config("[collections]\nplatform = %s\n" % first)
        collection_map = load_collection_map(
            config_path=config, overrides=["platform=%s" % second]
        )
        self.assertEqual(second, collection_map.get("platform"))

    def test_no_config_file_is_not_an_error(self):
        collection_map = load_collection_map(config_path=None, overrides=[])
        self.assertEqual(0, len(collection_map))

    def test_missing_explicit_config_file_is_an_error(self):
        self.assertRaises(
            CollectionMapError,
            load_collection_map,
            config_path=os.path.join(self.temp_dir, "nope.conf"),
        )

    def test_entry_is_not_checked_until_it_is_used(self):
        # A map may list collections or series that are not checked out; only
        # the ones a run actually needs have to be there.
        config = self.make_config("[collections]\nplatform = /nowhere\n")
        collection_map = load_collection_map(config_path=config)
        self.assertEqual("/nowhere", collection_map.get("platform"))


    def test_config_without_a_collections_section_is_an_error(self):
        config = self.make_config("[other]\nplatform = /nowhere\n")
        self.assertRaises(
            CollectionMapError, load_collection_map, config_path=config
        )

    def test_branch_for_path(self):
        platform = self.make_checkout("platform")
        config = self.make_config(
            "[collections]\nplatform.questing = %s\n" % platform
        )
        collection_map = load_collection_map(config_path=config)
        self.assertEqual(
            "platform.questing", collection_map.branch_for_path(platform)
        )
        self.assertIsNone(collection_map.branch_for_path(self.temp_dir))


class TestParseOverrides(TestCase):
    def test_parses(self):
        self.assertEqual(
            {"platform": os.path.abspath("/seeds/platform")},
            parse_overrides(["platform=/seeds/platform"]),
        )

    def test_rejects_malformed(self):
        self.assertRaises(CollectionMapError, parse_overrides, ["platform"])
        self.assertRaises(CollectionMapError, parse_overrides, ["=/seeds"])


class TestValidateEntry(TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.make_temp_dir()

    def test_accepts_a_seed_collection(self):
        directory = os.path.join(self.temp_dir, "platform")
        self.write(os.path.join(directory, "STRUCTURE"), "base:\n")
        validate_entry("platform", directory)

    def test_rejects_a_missing_directory(self):
        with self.assertRaises(CollectionMapError) as cm:
            validate_entry("platform", os.path.join(self.temp_dir, "nope"))
        self.assertIn("not a directory", str(cm.exception))

    def test_rejects_a_directory_without_a_structure_file(self):
        empty = os.path.join(self.temp_dir, "empty")
        os.makedirs(empty)
        with self.assertRaises(CollectionMapError) as cm:
            validate_entry("platform", empty)
        self.assertIn("STRUCTURE", str(cm.exception))

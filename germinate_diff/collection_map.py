"""The hand-maintained map of seed collections to local checkouts.

Germinate resolves the branches named by ``include`` lines in a ``STRUCTURE``
file against its seed sources, which for a remote run means fetching them from
Launchpad.  We want the opposite: every collection *except* the one under test
held fixed at a local checkout, so that the two germinate runs differ only in
the collection being changed.  Deriving those locations from ``STRUCTURE``
plus git remotes is out of scope for v1, so this is a small hand-maintained
map of branch name to local path.

The map is read from an ini file::

    [collections]
    platform.questing = ~/seeds/platform
    ubuntu.questing = ~/seeds/ubuntu

Keys are branch names exactly as they appear in ``include`` lines (and in
germinate's ``--seed-dist``).  Relative paths are taken relative to the
directory holding the config file.
"""

import configparser
import os

__all__ = [
    "CollectionMapError",
    "DEFAULT_CONFIG_PATHS",
    "CollectionMap",
    "load_collection_map",
    "validate_entry",
]


SECTION = "collections"


def _default_config_paths():
    paths = []
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if not xdg_config_home:
        xdg_config_home = os.path.join(os.path.expanduser("~"), ".config")
    paths.append(
        os.path.join(xdg_config_home, "germinate-diff", "collections.conf")
    )
    paths.append("/etc/germinate-diff/collections.conf")
    return paths


DEFAULT_CONFIG_PATHS = _default_config_paths()


class CollectionMapError(Exception):
    """The collection map could not be read or made sense of."""


class CollectionMap:
    """A mapping of seed branch name to local checkout directory."""

    def __init__(self, entries=None, source=None):
        self._entries = dict(entries or {})
        self.source = source

    def __len__(self):
        return len(self._entries)

    def __contains__(self, branch):
        return branch in self._entries

    def __iter__(self):
        return iter(sorted(self._entries))

    def get(self, branch):
        """Return the local directory for ``branch``, or ``None``."""
        return self._entries.get(branch)

    def items(self):
        return sorted(self._entries.items())

    def update(self, entries):
        self._entries.update(entries)

    def branch_for_path(self, path):
        """Return the branch name whose checkout is ``path``, or ``None``.

        This lets ``germinate-diff /path/to/platform old new chdist`` work out
        that it is the platform collection under test, without the user having
        to repeat the branch name on the command line.
        """
        target = os.path.realpath(path)
        for branch, directory in sorted(self._entries.items()):
            if os.path.realpath(directory) == target:
                return branch
        return None


def validate_entry(branch, directory):
    """Check that a mapped directory really holds a seed collection.

    Entries are checked when they are used rather than when the map is
    loaded, so that a map listing collections or series you have not checked
    out does not break runs that do not need them.
    """
    if not os.path.isdir(directory):
        raise CollectionMapError(
            "collection %s: %s is not a directory" % (branch, directory)
        )
    structure = os.path.join(directory, "STRUCTURE")
    if not os.path.isfile(structure):
        raise CollectionMapError(
            "collection %s: %s has no STRUCTURE file" % (branch, directory)
        )


def parse_overrides(overrides):
    """Parse ``NAME=PATH`` strings from the command line."""
    entries = {}
    for override in overrides:
        branch, sep, path = override.partition("=")
        if not sep or not branch.strip() or not path.strip():
            raise CollectionMapError(
                "malformed --collection %r, expected NAME=PATH" % override
            )
        entries[branch.strip()] = os.path.abspath(
            os.path.expanduser(os.path.expandvars(path.strip()))
        )
    return entries


def _read_config(path):
    parser = configparser.ConfigParser()
    # Branch names are case-sensitive; configparser lowercases keys by
    # default, which would turn "ubuntu.Questing" into something that never
    # matches an include line.
    parser.optionxform = str
    try:
        with open(path, encoding="UTF-8") as f:
            parser.read_file(f, source=path)
    except OSError as e:
        raise CollectionMapError("could not read %s: %s" % (path, e))
    except configparser.Error as e:
        raise CollectionMapError("could not parse %s: %s" % (path, e))

    if not parser.has_section(SECTION):
        raise CollectionMapError(
            "%s has no [%s] section" % (path, SECTION)
        )

    base = os.path.dirname(os.path.abspath(path))
    entries = {}
    for branch, value in parser.items(SECTION):
        value = os.path.expanduser(os.path.expandvars(value.strip()))
        if not value:
            raise CollectionMapError(
                "collection %s in %s has an empty path" % (branch, path)
            )
        entries[branch] = os.path.abspath(os.path.join(base, value))
    return entries


def load_collection_map(config_path=None, overrides=()):
    """Load the collection map.

    ``config_path`` names an explicit config file; if it is ``None`` the
    default locations are tried in turn and the first that exists is used.
    ``overrides`` is a sequence of ``NAME=PATH`` strings that are applied on
    top of whatever the file provided.  It is not an error for there to be no
    config file at all: a collection with no ``include`` lines needs no map.
    Entries are not checked here; see :func:`validate_entry`.
    """
    entries = {}
    source = None
    if config_path is not None:
        if not os.path.exists(config_path):
            raise CollectionMapError(
                "collection map %s does not exist" % config_path
            )
        entries = _read_config(config_path)
        source = config_path
    else:
        for candidate in DEFAULT_CONFIG_PATHS:
            if os.path.exists(candidate):
                entries = _read_config(candidate)
                source = candidate
                break

    entries.update(parse_overrides(overrides))

    return CollectionMap(entries, source=source)

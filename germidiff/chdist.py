"""Creating and refreshing the chdist a germination reads the archive from.

Germinate is pointed at archive metadata through an ``APT_CONFIG``, which for
germidiff means a chdist.  Running over a merge proposal has to produce one
without being told where it is, so this derives it from the proposal: the
series names the archive, and the collection decides which components are
visible.

That second half matters more than it looks.  Germinate ignores
``--components`` when it is given an ``--apt-config``, so what a run can see
is decided entirely by the chdist's ``sources.list`` -- and germinating the
platform or ubuntu seeds against a chdist that carries universe quietly
resolves dependencies into packages that are not there for them.  The result
is a plausible report that is wrong, which is why a chdist whose components
do not match is an error rather than a note.
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

import logging
import os
import subprocess

__all__ = [
    "ALL_COMPONENTS",
    "ChdistError",
    "DEFAULT_MIRROR",
    "MAIN_COMPONENTS",
    "MAIN_ONLY_COLLECTIONS",
    "chdist_base",
    "chdist_name",
    "components_for_collection",
    "ensure_chdist",
]

_logger = logging.getLogger("germidiff")

DEFAULT_MIRROR = "http://archive.ubuntu.com/ubuntu"

MAIN_COMPONENTS = ("main", "restricted")
ALL_COMPONENTS = ("main", "restricted", "universe", "multiverse")

# The collections whose seeds are a promise about what Ubuntu supports, and
# so may only pull from the components Ubuntu supports.  Flavours seed from
# the whole archive.
MAIN_ONLY_COLLECTIONS = frozenset({"ubuntu", "platform"})


class ChdistError(Exception):
    """A chdist could not be created, checked, or updated."""


def chdist_base(base=None):
    """Where germidiff's chdists live.

    Deliberately not ``$CHDIST_HOME`` or ``~/.chdist``.  germidiff creates
    chdists of its own, named after the series and its components, and those
    names are exactly the ones somebody germinating by hand would have picked
    too -- so sharing a directory with the chdists you keep for yourself
    means germidiff either refuses to run because yours carries the wrong
    components, or quietly updates a chdist you were holding still.  It keeps
    its own instead, and reaches yours only when told to.
    """
    if base is None:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        if not cache_home:
            cache_home = os.path.join(os.path.expanduser("~"), ".cache")
        base = os.path.join(cache_home, "germidiff", "chdists")
    return os.path.abspath(os.path.expanduser(base))


def components_for_collection(collection, overrides=None):
    """Which archive components a collection's seeds may resolve against."""
    if overrides and collection in overrides:
        return tuple(overrides[collection])
    if collection in MAIN_ONLY_COLLECTIONS:
        return MAIN_COMPONENTS
    return ALL_COMPONENTS


def chdist_name(series, components):
    """The name of the chdist for a series and component set.

    One chdist per series is not enough, because the same series has to be
    germinated with different components depending on the collection.  The
    main-and-restricted one takes the series' own name and the one carrying
    everything is suffixed, which is the convention these get created under
    by hand anyway.
    """
    if tuple(components) == MAIN_COMPONENTS:
        return series
    if tuple(components) == ALL_COMPONENTS:
        return "%s-all" % series
    return "%s-%s" % (series, "+".join(components))


def _sources_components(path, series):
    """The components a sources.list offers for ``series``, or ``None``.

    ``None`` means the file says nothing about that series at all, which is a
    different problem from it offering the wrong components.
    """
    found = None
    try:
        with open(path, encoding="UTF-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        raise ChdistError("could not read %s: %s" % (path, e))

    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        # "deb [opts] URI SUITE component..."
        if words[0] not in ("deb", "deb-src"):
            continue
        words = words[1:]
        while words and words[0].startswith("["):
            closing = [i for i, w in enumerate(words) if w.endswith("]")]
            if not closing:
                break
            words = words[closing[0] + 1:]
        if len(words) < 2 or words[1] != series:
            continue
        if found is None:
            found = set()
        found.update(words[2:])
    return found


def _run_chdist(base, *args):
    command = ["chdist", "-d", base] + list(args)
    _logger.debug("running %s", " ".join(command))
    try:
        proc = subprocess.run(
            command, capture_output=True, encoding="UTF-8", errors="replace"
        )
    except OSError as e:
        raise ChdistError(
            "could not run chdist: %s (it comes from the devscripts "
            "package)" % e
        )
    if proc.returncode != 0:
        raise ChdistError(
            "%s failed with exit status %d:\n%s"
            % (" ".join(command), proc.returncode, proc.stderr.strip())
        )
    return proc.stdout


def ensure_chdist(
    series,
    components,
    arch,
    base=None,
    mirror=DEFAULT_MIRROR,
    update=True,
    check_components=True,
    name=None,
):
    """Make sure a suitable chdist exists and its lists are current.

    Returns the chdist's name.  An existing chdist is reused rather than
    rebuilt, but only after checking it really offers ``series`` with
    ``components``; set ``check_components`` false to go ahead anyway.
    """
    base = chdist_base(base)
    components = tuple(components)
    if name is None:
        name = chdist_name(series, components)
    directory = os.path.join(base, name)
    sources = os.path.join(directory, "etc", "apt", "sources.list")

    if os.path.isdir(directory):
        if not os.path.isfile(sources):
            raise ChdistError(
                "%s exists but has no etc/apt/sources.list; it is not a "
                "chdist, and germidiff will not replace it" % directory
            )
        if check_components:
            _check_existing(name, sources, series, components)
        _logger.info("using chdist %s (%s)", name, directory)
    else:
        _logger.info(
            "creating chdist %s for %s %s",
            name, series, " ".join(components),
        )
        # chdist resolves its data directory with abs_path(), which gives up
        # on a directory that does not exist yet and leaves it creating the
        # chdist in the wrong place entirely.  Ours is somewhere it made no
        # sense for anything else to have created, so make it first.
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            raise ChdistError("could not create %s: %s" % (base, e))
        _run_chdist(
            base, "-a", arch, "create", name, mirror, series, *components
        )
        # A chdist with no lists at all is useless, whatever was asked for.
        update = True

    if update:
        _logger.info("updating apt lists for %s", name)
        _run_chdist(base, "apt-get", name, "update")

    return name


def _check_existing(name, sources, series, components):
    have = _sources_components(sources, series)
    if have is None:
        raise ChdistError(
            "chdist %s does not carry %s (see %s); germidiff will not "
            "rewrite a chdist you made, so point it at another one with "
            "--chdist or move this one aside"
            % (name, series, sources)
        )
    want = set(components)
    if have != want:
        raise ChdistError(
            "chdist %s carries %s for %s, but this collection germinates "
            "against %s; germinate takes its components from the chdist "
            "alone, so this would quietly resolve against the wrong archive "
            "(see %s, or pass --no-check-components to go ahead anyway)"
            % (
                name,
                " ".join(sorted(have)),
                series,
                " ".join(components),
                sources,
            )
        )

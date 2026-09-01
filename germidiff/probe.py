"""Cutting a Recommends and re-germinating, to see what really falls out.

The retention check says a package is held only by a ``Recommends``.  That is
germinate's own single ``Why``, so it is a strong hint rather than proof, and
it says nothing about what else would follow the package out.  This answers
both by germinating again with that one edge removed and diffing the result.

It costs a full germination per edge, which is why it is opt-in.  Germinate
is driven in-process here rather than through its command line, because
there is no way to ask the germinate CLI to ignore a single dependency.  It
goes through the documented :class:`germinate.archive.Archive` interface --
germinate reads a section only through ``section["Package"]`` and
``section.get(...)``, so substituting a plain dict with one field rewritten
is enough, and no private state is touched.
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
from contextlib import contextmanager

from germidiff.diff import diff_runs
from germidiff.runner import GerminateRun

__all__ = [
    "ProbeError",
    "ProbeResult",
    "probe_cuts",
]

_logger = logging.getLogger("germidiff")


class ProbeError(Exception):
    """The probe could not be run at all."""


class ProbeResult:
    """What cutting a set of dependencies did, per seed.

    ``diff`` is the full per-seed comparison against the run being explained.
    The global union is the least interesting part of it: a package can leave
    several seeds -- and so several images -- while staying in the archive
    because some other seed still pulls it in.
    """

    def __init__(self, cuts, diff=None, after=None, error=None):
        #: The (holder, field, package) dependencies that were cut.
        self.cuts = list(cuts)
        self.diff = diff
        #: The run without those dependencies, for diffing against directly.
        self.after = after
        self.error = error


def _dependency_name(alternative):
    """The package name from one alternative of a dependency field."""
    alternative = alternative.strip()
    if not alternative:
        return ""
    # e.g. "gcc (>= 4:4.9)", "libc6-dev [amd64]", "foo:any"
    return alternative.split()[0].split(":")[0]


def drop_alternative(value, target):
    """Remove ``target`` from a dependency field, keeping other alternatives.

    Commas separate groups and only groups, so splitting on them is safe.
    Removing just the target rather than its whole group matters: cutting
    ``make | build-essential`` down to nothing would also take away ``make``,
    which is a different question from the one being asked.
    """
    groups = []
    for group in value.split(","):
        kept = [
            alternative.strip()
            for alternative in group.split("|")
            if alternative.strip()
            and _dependency_name(alternative) != target
        ]
        if kept:
            groups.append(" | ".join(kept))
    return ", ".join(groups)


def _as_dict(section):
    try:
        return dict(section)
    except (TypeError, ValueError):
        return {key: section[key] for key in section.keys()}


def _cutting_archive(inner, cuts):
    """Wrap an Archive, dropping dependencies from it as it streams.

    ``cuts`` maps a package name to a list of ``(field, target)`` pairs to
    remove from it.
    """
    from germinate.archive import Archive, IndexType

    class Cutting(Archive):
        def sections(self):
            for index_type, section in inner.sections():
                if index_type == IndexType.PACKAGES:
                    try:
                        name = section["Package"]
                    except (KeyError, TypeError):
                        name = None
                    if name in cuts:
                        section = _as_dict(section)
                        for field, target in cuts[name]:
                            section[field] = drop_alternative(
                                section.get(field, ""), target
                            )
                yield index_type, section

    return Cutting()


@contextmanager
def _quiet_germinate():
    """Keep germinate's own logging out of ours while probing.

    Germinate logs to a logger of its own, which would otherwise appear under
    germidiff's prefix as though we had said it.  These are the same
    messages the run being explained already produced, and that run's output
    is the authoritative copy (kept with --keep).  Silencing them here does
    not hide a probe going wrong: a probe that fails raises, and one that
    quietly germinates something other than the run being explained is caught
    by the baseline check in :func:`probe_edges`.
    """
    logger = logging.getLogger("germinate")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _abi_tag(arch):
    """The architecture germinate resolves dependencies against."""
    try:
        from germinate.variants import resolve

        return resolve(arch, None)[0]
    except ImportError:
        pass
    try:
        from germinate.scripts.germinate_main import isa_tag_to_abi_tag

        return isa_tag_to_abi_tag.get(arch, arch)
    except ImportError:
        return arch


def _germinate(seed_base, seed_dist, apt_config, arch, cuts=None):
    """Germinate in-process, returning a run's per-seed expanded lists."""
    from germinate.archive import AptArchive
    from germinate.germinator import Germinator
    from germinate.seeds import Seed, SeedError, SeedStructure

    germinator = Germinator(_abi_tag(arch))
    archive = AptArchive(apt_config)
    if cuts:
        archive = _cutting_archive(archive, cuts)
    germinator.parse_archive(archive)

    structure = SeedStructure(seed_dist, [seed_base], None)
    germinator.plant_seeds(structure)

    # Germinate looks for a seed named "blacklist" unless a file called
    # "blocklist" exists in its working directory, which for a germidiff
    # run never happens; match that rather than being cleverer than the runs
    # we are explaining.
    try:
        with Seed([seed_base], seed_dist, "blacklist", None) as blocklist:
            germinator.parse_blocklist(structure, blocklist)
    except SeedError:
        pass

    germinator.grow(structure)
    germinator.add_extras(structure)

    seeds = {
        name: set(germinator.get_full(structure, name))
        for name in structure.names
    }
    # inner_seeds is germinate's own expansion, and includes the seed
    # itself; GerminateRun.inherit holds only what a seed inherits.
    inherit = {
        name: [s for s in structure.inner_seeds(name) if s != name]
        for name in structure.names
    }
    return GerminateRun(
        "probe", None, list(structure.names), seeds, inherit=inherit
    )


def probe_cuts(
    cuts, seed_base, seed_dist, apt_config, arch, baseline=None
):
    """Germinate again without ``cuts``, and diff the result per seed.

    ``cuts`` is a sequence of ``(package, field, target)`` triples naming
    dependencies to remove.  They are cut together rather than one at a
    time, so the answer describes one coherent world rather than a series of
    unrelated hypotheticals.

    ``baseline`` is the run being explained; germinating in-process must
    reproduce it, or the comparison would be against a different germination
    and its numbers would not mean what they appear to.
    """
    if not cuts:
        return None

    try:
        import germinate.archive  # noqa: F401
        import germinate.germinator  # noqa: F401
    except ImportError as e:
        raise ProbeError(
            "the retention probe needs germinate importable by this Python "
            "(%s)" % e
        )

    _logger.info("probing: germinating twice more")
    with _quiet_germinate():
        try:
            reference = _germinate(seed_base, seed_dist, apt_config, arch)
        except Exception as e:
            raise ProbeError("could not germinate in-process: %s" % e)

    if baseline is not None and reference.union() != baseline:
        # Without this the probe would answer a question about a different
        # germination than the one being reported, which is worse than
        # answering none: usually it means the importable germinate is not
        # the executable that was run.
        raise ProbeError(
            "germinating in-process does not reproduce the run being "
            "explained (%d packages against %d); the importable germinate "
            "is probably not the one --germinate ran"
            % (len(reference.union()), len(baseline))
        )

    by_package = {}
    for package, field, target in cuts:
        by_package.setdefault(package, []).append((field, target))

    with _quiet_germinate():
        try:
            after = _germinate(
                seed_base, seed_dist, apt_config, arch, cuts=by_package
            )
        except Exception as e:
            return ProbeResult(cuts, error=str(e))

    return ProbeResult(cuts, diff=diff_runs(reference, after), after=after)

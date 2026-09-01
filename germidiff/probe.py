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

import logging
from contextlib import contextmanager

__all__ = [
    "ProbeError",
    "ProbeResult",
    "probe_edges",
]

_logger = logging.getLogger("germidiff")


class ProbeError(Exception):
    """The probe could not be run at all."""


class ProbeResult:
    """What cutting one ``holder`` -> ``package`` Recommends did."""

    def __init__(self, holder, package, removed=(), error=None):
        self.holder = holder
        self.package = package
        #: Packages that left the seeds' expanded lists, sorted.
        self.removed = sorted(removed)
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


def _cutting_archive(inner, holder, target):
    """Wrap an Archive, rewriting one package's Recommends as it streams."""
    from germinate.archive import Archive, IndexType

    class CutRecommends(Archive):
        def sections(self):
            for index_type, section in inner.sections():
                if index_type == IndexType.PACKAGES:
                    try:
                        name = section["Package"]
                    except (KeyError, TypeError):
                        name = None
                    if name == holder:
                        section = _as_dict(section)
                        section["Recommends"] = drop_alternative(
                            section.get("Recommends", ""), target
                        )
                yield index_type, section

    return CutRecommends()


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


def _germinate(seed_base, seed_dist, apt_config, arch, cut=None):
    """Germinate in-process and return the union of the seeds' full lists."""
    from germinate.archive import AptArchive
    from germinate.germinator import Germinator
    from germinate.seeds import Seed, SeedError, SeedStructure

    germinator = Germinator(_abi_tag(arch))
    archive = AptArchive(apt_config)
    if cut is not None:
        archive = _cutting_archive(archive, *cut)
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

    union = set()
    for name in structure.names:
        union |= germinator.get_full(structure, name)
    return union


def probe_edges(edges, seed_base, seed_dist, apt_config, arch, baseline=None):
    """Cut each ``(holder, package)`` Recommends in turn and report the loss.

    ``baseline`` is the union of the new run's expanded lists, used only to
    check that germinating in-process reproduces what the command-line run
    produced; a mismatch means the importable germinate is not the one that
    was run, and makes the probe's numbers untrustworthy.
    """
    if not edges:
        return []

    try:
        import germinate.archive  # noqa: F401
        import germinate.germinator  # noqa: F401
    except ImportError as e:
        raise ProbeError(
            "the retention probe needs germinate importable by this Python "
            "(%s)" % e
        )

    _logger.info("probing: germinating %d more time(s)", len(edges) + 1)
    with _quiet_germinate():
        try:
            reference = _germinate(seed_base, seed_dist, apt_config, arch)
        except Exception as e:
            raise ProbeError("could not germinate in-process: %s" % e)

    if baseline is not None and reference != baseline:
        # Without this the probe would answer a question about a different
        # germination than the one being reported, which is worse than
        # answering none: usually it means the importable germinate is not
        # the executable that was run.
        raise ProbeError(
            "germinating in-process does not reproduce the run being "
            "explained (%d packages against %d); the importable germinate "
            "is probably not the one --germinate ran"
            % (len(reference), len(baseline))
        )

    results = []
    with _quiet_germinate():
        for holder, package in edges:
            try:
                cut = _germinate(
                    seed_base,
                    seed_dist,
                    apt_config,
                    arch,
                    cut=(holder, package),
                )
            except Exception as e:
                results.append(ProbeResult(holder, package, error=str(e)))
                continue
            results.append(ProbeResult(holder, package, reference - cut))
    return results

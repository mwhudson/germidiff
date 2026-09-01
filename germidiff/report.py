"""Rendering a diff as text that can be pasted into a Launchpad MP comment.

Launchpad merge proposal comments are plain text with no rich formatting, so
the output is plain text with light markdown-ish styling: ``**seedname**``
headers and ``+pkg`` / ``-pkg`` lines, readable as-is whether or not anything
renders it.  Nothing here relies on columns lining up, since a comment may be
rendered in a proportional font.
"""

import textwrap

from germidiff.diff import NEW_SEED, REMOVED_SEED

__all__ = [
    "NO_CHANGES",
    "NO_GLOBAL_CHANGES",
    "format_diff",
]

NO_CHANGES = "No changes to any expanded package list."

# Shown under an empty global section, which happens when packages move
# between seeds without the set of packages as a whole changing.  Saying so
# is more use to a reviewer than an unexplained empty heading.
NO_GLOBAL_CHANGES = "(no net change across all seeds)"

RETAINED_HEADER = "**no longer seeded, still pulled in**"
PROBE_HEADER = "**retention probe**"

_PRESENCE_LABELS = {
    NEW_SEED: " (new seed)",
    REMOVED_SEED: " (removed seed)",
}

# Holder lists are for orientation, not completeness; the full picture is a
# germinate run away and an MP comment is not the place for it.
_MAX_HOLDERS = 4
_WRAP = 72


def _packages(count):
    return "%d package%s" % (count, "" if count == 1 else "s")


def _whole_seed_lines(seed_diff):
    """Render a seed the change added or removed.

    Its ``+``/``-`` list is the seed's entire expansion rather than a delta,
    and most of it is usually noise: the packages are in the archive either
    way and only the seed accounting for them moved.  So lead with the ones
    that genuinely came or went, and count the rest.
    """
    new = seed_diff.presence == NEW_SEED
    total = len(seed_diff.added if new else seed_diff.removed)
    if not total:
        return ["no packages"]

    shown = seed_diff.entered if new else seed_diff.left
    elsewhere = seed_diff.elsewhere
    moved = (
        "already pulled in by other seeds"
        if new
        else "still pulled in by other seeds"
    )

    if not shown:
        return ["%s, all %s" % (_packages(total), moved)]

    lines = ["%s%s" % ("+" if new else "-", pkg) for pkg in shown]
    if elsewhere:
        lines.append("and %d more, %s" % (elsewhere, moved))
    return lines


def _format_section(seed_diff, empty_note=None, whole_lists=False):
    label = _PRESENCE_LABELS.get(seed_diff.presence, "")
    lines = ["**%s**%s" % (seed_diff.name, label)]
    if seed_diff.presence and not whole_lists:
        lines.extend(_whole_seed_lines(seed_diff))
        return lines
    lines.extend("+%s" % pkg for pkg in seed_diff.added)
    lines.extend("-%s" % pkg for pkg in seed_diff.removed)
    if len(lines) == 1 and empty_note is not None:
        lines.append(empty_note)
    return lines


def _join_holders(holders):
    shown = holders[:_MAX_HOLDERS]
    text = ", ".join(shown)
    remaining = len(holders) - len(shown)
    if remaining:
        text += " and %d more" % remaining
    return text


def _wrap(text, indent="  "):
    return textwrap.wrap(
        text, width=_WRAP, initial_indent=indent, subsequent_indent=indent
    ) or [indent + text]


def format_retained(retained):
    """Render the packages a change stopped seeding but did not remove."""
    if not retained:
        return []

    lines = [RETAINED_HEADER]
    soft = [entry for entry in retained if entry.soft]
    hard = [entry for entry in retained if not entry.soft]

    for entry in soft:
        lines.append(
            "! %s: only by %s (Recommends)"
            % (entry.package, _join_holders(entry.holders))
        )
    if hard:
        # The all-clear has to read as one.  Saying the same thing either way
        # and letting the "!" lines above be the only difference makes a
        # change that fixed the problem look like the change that caused it.
        names = ", ".join(entry.package for entry in hard)
        if soft:
            lines.extend(
                _wrap("%d other%s held by hard dependencies: %s"
                      % (len(hard), "" if len(hard) == 1 else "s", names))
            )
        else:
            lines.extend(
                _wrap("all %d held by hard dependencies: %s"
                      % (len(hard), names), indent="")
            )
    return lines


def format_probes(probes):
    """Render the results of cutting each soft edge in turn."""
    if not probes:
        return []

    lines = [PROBE_HEADER]
    for probe in probes:
        if probe.error is not None:
            lines.extend(
                _wrap(
                    "could not probe %s's Recommends of %s: %s"
                    % (probe.holder, probe.package, probe.error),
                    indent="",
                )
            )
            continue
        if not probe.removed:
            lines.append(
                "cutting %s's Recommends of %s removes nothing; it is held "
                "by something else too" % (probe.holder, probe.package)
            )
            continue
        lines.append(
            "cutting %s's Recommends of %s removes %d package(s):"
            % (probe.holder, probe.package, len(probe.removed))
        )
        lines.extend("-%s" % pkg for pkg in probe.removed)
    return lines


def format_diff(diff, retained=(), probes=(), whole_seed_lists=False):
    """Render a diff, with any retention findings, as plain text.

    The global section comes first, then one section per changed seed;
    unchanged seeds are omitted.  A seed the change added or removed is
    summarised rather than listed in full, unless ``whole_seed_lists``.
    Retention findings follow, and are reported even when nothing changed --
    a change that leaves the expanded lists alone but makes a package's
    presence rest on a Recommends is exactly the case the diff alone cannot
    show.  The returned string ends with a newline.
    """
    sections = []
    if diff.changed:
        sections.append(_format_section(diff.global_diff, NO_GLOBAL_CHANGES))
        for seed_diff in diff.changed_seeds:
            sections.append(
                _format_section(seed_diff, whole_lists=whole_seed_lists)
            )
    else:
        sections.append([NO_CHANGES])

    retained_lines = format_retained(retained)
    if retained_lines:
        sections.append(retained_lines)
    probe_lines = format_probes(probes)
    if probe_lines:
        sections.append(probe_lines)

    return "\n\n".join("\n".join(section) for section in sections) + "\n"

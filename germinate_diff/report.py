"""Rendering a diff as text that can be pasted into a Launchpad MP comment.

Launchpad merge proposal comments are plain text with no rich formatting, so
the output is plain text with light markdown-ish styling: ``**seedname**``
headers and ``+pkg`` / ``-pkg`` lines, readable as-is whether or not anything
renders it.
"""

from germinate_diff.diff import NEW_SEED, REMOVED_SEED

__all__ = ["NO_CHANGES", "NO_GLOBAL_CHANGES", "format_diff"]

NO_CHANGES = "No changes to any expanded package list."

# Shown under an empty global section, which happens when packages move
# between seeds without the set of packages as a whole changing.  Saying so
# is more use to a reviewer than an unexplained empty heading.
NO_GLOBAL_CHANGES = "(no net change across all seeds)"

_PRESENCE_LABELS = {
    NEW_SEED: " (new seed)",
    REMOVED_SEED: " (removed seed)",
}


def _format_section(seed_diff, empty_note=None):
    label = _PRESENCE_LABELS.get(seed_diff.presence, "")
    lines = ["**%s**%s" % (seed_diff.name, label)]
    lines.extend("+%s" % pkg for pkg in seed_diff.added)
    lines.extend("-%s" % pkg for pkg in seed_diff.removed)
    if len(lines) == 1 and empty_note is not None:
        lines.append(empty_note)
    return lines


def format_diff(diff):
    """Render a :class:`germinate_diff.diff.Diff` as plain text.

    The global section comes first, then one section per changed seed;
    unchanged seeds are omitted.  The returned string ends with a newline.
    """
    if not diff.changed:
        return NO_CHANGES + "\n"

    sections = [_format_section(diff.global_diff, NO_GLOBAL_CHANGES)]
    for seed_diff in diff.changed_seeds:
        sections.append(_format_section(seed_diff))

    return "\n\n".join("\n".join(section) for section in sections) + "\n"

#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Compare the files two installations wrote, and show the differences."""

from __future__ import annotations

import difflib
import json
import pathlib
import re

# Values on the pages that change from one run to the next, even with the clock
# frozen, because they come from the operating system.
MASKS = (
    # Seasons, the uptime of the server and of WeeWX, by the minute.
    (re.compile(rb'\d+ days?, \d+ hours?, \d+ minutes?'), b'UPTIME'),
    # Belchertown, the uptime of the server in its JSON.
    (re.compile(rb'"serveruptime": "\d+ seconds"'), b'"serveruptime": "UPTIME"'),
)

# A diff longer than this is cut, per file.
MAX_DIFF_LINES = 200


def _read(directory: pathlib.Path, name: str, versions: list[bytes]) -> bytes | None:
    """Return a file with the version numbers and the MASKS replaced, or None if missing.

    Pages name the WeeWX version they were made with, and the skins name their own
    version, e.g. "Seasons 5.5.2". With the skins of the baseline, that is the
    baseline's version on both sides. So every version number is replaced in every
    file.
    """
    path = directory / name
    if not path.is_file():
        return None
    content = path.read_bytes()
    for version in versions:
        content = content.replace(version, b'VERSION')
    for pattern, replacement in MASKS:
        content = pattern.sub(replacement, content)
    return content


def names(directory: pathlib.Path) -> set[str]:
    """Return the paths of all files below directory, relative to it."""
    return {str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file()}


def differing_files(first: pathlib.Path, second: pathlib.Path, versions: list[str]) -> list[str]:
    """Return the files that differ between two output directories.

    A file that exists on one side only counts as differing. versions are the WeeWX
    version numbers to replace before the comparison.
    """
    encoded = [version.encode() for version in versions]
    return sorted(
        name
        for name in names(first) | names(second)
        if _read(first, name, encoded) != _read(second, name, encoded)
    )


def _text_lines(content: bytes, name: str) -> list[str] | None:
    """Return the lines of a text file, or None for a binary file.

    JSON is indented first. Belchertown writes its JSON on one line, and a diff of
    one line shows nothing.
    """
    if b'\0' in content:
        return None
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return None
    if name.endswith('.json'):
        try:
            text = json.dumps(json.loads(text), indent=1, sort_keys=True)
        except ValueError:
            pass
    return text.splitlines(keepends=True)


def describe_differences(
    first: pathlib.Path,
    second: pathlib.Path,
    differing: list[str],
    versions: list[str],
    labels: tuple[str, str],
) -> str:
    """Return a unified diff of the differing files, as text for a .diff file."""
    encoded = [version.encode() for version in versions]
    parts = []
    for name in differing:
        old = _read(first, name, encoded)
        new = _read(second, name, encoded)
        if old is None or new is None:
            parts.append('Only in %s: %s\n' % (labels[0] if new is None else labels[1], name))
            continue
        old_lines = _text_lines(old, name)
        new_lines = _text_lines(new, name)
        if old_lines is None or new_lines is None:
            parts.append(
                'Binary files %s/%s and %s/%s differ\n' % (labels[0], name, labels[1], name)
            )
            continue
        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                '%s/%s' % (labels[0], name),
                '%s/%s' % (labels[1], name),
                n=2,
            )
        )
        if len(diff) > MAX_DIFF_LINES:
            diff = diff[:MAX_DIFF_LINES] + ['... %d more lines\n' % (len(diff) - MAX_DIFF_LINES)]
        parts.append(''.join(line if line.endswith('\n') else line + '\n' for line in diff))
    return ''.join(parts)

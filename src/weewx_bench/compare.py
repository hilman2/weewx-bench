#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Compare the databases and files two installations wrote, and show the differences."""

from __future__ import annotations

import difflib
import json
import pathlib
import re
import sqlite3
import time

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


# Examples per column in a diff of two databases.
MAX_EXAMPLES = 5


def _tables(connection: sqlite3.Connection, schema: str) -> dict[str, list[tuple[str, int]]]:
    """Return {table: [(column, position in the primary key)]} for one attached schema."""
    tables = {}
    for (name,) in connection.execute(
        "SELECT name FROM %s.sqlite_master WHERE type = 'table'" % schema
    ):
        info = connection.execute('PRAGMA %s.table_info(%s)' % (schema, name)).fetchall()
        tables[name] = [(row[1], row[5]) for row in info]
    return tables


def compare_databases(first: pathlib.Path, second: pathlib.Path) -> dict[str, dict[str, int]]:
    """Return how many values differ between two WeeWX SQLite databases.

    The result is {table: {column: rows}}. Rows that exist on one side only are
    counted under '(only in first)' and '(only in second)', a table or column that
    exists on one side only under '(missing)'. Tables without differences are left
    out. Values are compared exactly, floats included.
    """
    result: dict[str, dict[str, int]] = {}
    with sqlite3.connect('file:%s?mode=ro' % first, uri=True) as connection:
        connection.execute("ATTACH DATABASE 'file:%s?mode=ro' AS other" % second)
        mine = _tables(connection, 'main')
        theirs = _tables(connection, 'other')
        for table in sorted(set(mine) | set(theirs)):
            if table not in mine or table not in theirs:
                result[table] = {'(missing)': 1}
                continue
            differences: dict[str, int] = {}
            columns = [column for column, _ in mine[table]]
            other_columns = [column for column, _ in theirs[table]]
            for column in sorted(set(columns) ^ set(other_columns)):
                differences[column + ' (missing)'] = 1
            keys = [column for column, position in mine[table] if position]
            common = [c for c in columns if c in other_columns and c not in keys]
            if keys:
                join = ' AND '.join('a.%s = b.%s' % (key, key) for key in keys)
                for label, left, right in (('first', 'main', 'other'), ('second', 'other', 'main')):
                    (count,) = connection.execute(
                        'SELECT COUNT(*) FROM %s.%s AS a WHERE NOT EXISTS '
                        '(SELECT 1 FROM %s.%s AS b WHERE %s)' % (left, table, right, table, join)
                    ).fetchone()
                    if count:
                        differences['(only in %s)' % label] = count
                if common:
                    sums = ', '.join('SUM(a.%s IS NOT b.%s)' % (c, c) for c in common)
                    counts = connection.execute(
                        'SELECT %s FROM main.%s AS a JOIN other.%s AS b ON %s'
                        % (sums, table, table, join)
                    ).fetchone()
                    for column, count in zip(common, counts, strict=True):
                        if count:
                            differences[column] = count
            if differences:
                result[table] = differences
    return result


def describe_database_differences(
    first: pathlib.Path,
    second: pathlib.Path,
    differences: dict[str, dict[str, int]],
    labels: tuple[str, str],
) -> str:
    """Return the differences of compare_databases() as text, with a few examples each."""
    lines = []
    with sqlite3.connect('file:%s?mode=ro' % first, uri=True) as connection:
        connection.execute("ATTACH DATABASE 'file:%s?mode=ro' AS other" % second)
        tables = _tables(connection, 'main')
        for table, columns in differences.items():
            for column, count in columns.items():
                lines.append('%s.%s: %d rows differ' % (table, column, count))
                keys = [name for name, position in tables.get(table, []) if position]
                if not keys or column.startswith('(') or column.endswith('(missing)'):
                    continue
                join = ' AND '.join('a.%s = b.%s' % (key, key) for key in keys)
                rows = connection.execute(
                    'SELECT a.%s, a.%s, b.%s FROM main.%s AS a JOIN other.%s AS b ON %s '
                    'WHERE a.%s IS NOT b.%s ORDER BY 1 LIMIT %d'
                    % (keys[0], column, column, table, table, join, column, column, MAX_EXAMPLES)
                ).fetchall()
                for key, old, new in rows:
                    if keys[0] == 'dateTime':
                        key = time.strftime('%Y-%m-%d %H:%M', time.gmtime(key))
                    lines.append('    %s  %s: %r  %s: %r' % (key, labels[0], old, labels[1], new))
    return '\n'.join(lines) + '\n' if lines else ''

#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Count the SQL statements a WeeWX process sends to its database.

weewx-bench copies this module into the site-packages of every benchmarked WeeWX
installation, together with a .pth file that imports it at interpreter start. It
does nothing unless the environment variable WEEWX_BENCH_TRACE names a file. Then
it counts every statement by its first keyword and writes the counts to that file,
as JSON, when the interpreter exits.

The hooks sit in the database drivers, sqlite3 and PyMySQL, not in weedb. That way
they work for every WeeWX version, whatever weedb looks like inside.

Counting slows the process down, so a run with WEEWX_BENCH_TRACE set is never a
timed run.
"""

from __future__ import annotations

import os

_trace_path = os.environ.get('WEEWX_BENCH_TRACE')


def _install(trace_path: str) -> None:
    import atexit
    import collections
    import json
    import sqlite3

    counts: collections.Counter[str] = collections.Counter()

    def record(statement: str) -> None:
        words = statement.split(None, 1)
        counts[words[0].upper() if words else '?'] += 1

    original_connect = sqlite3.connect

    def connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        connection = original_connect(*args, **kwargs)
        # The callback also sees the BEGIN and COMMIT that the sqlite3 module issues
        # on its own. They are round trips too, so they are counted.
        connection.set_trace_callback(record)
        return connection

    sqlite3.connect = connect

    try:
        import pymysql.cursors
    except ImportError:
        pass
    else:
        original_execute = pymysql.cursors.Cursor.execute

        def execute(self, query, args=None):  # type: ignore[no-untyped-def]
            record(query)
            return original_execute(self, query, args)

        pymysql.cursors.Cursor.execute = execute

    def write() -> None:
        with open(trace_path, 'w') as trace_file:
            json.dump(dict(counts), trace_file)

    atexit.register(write)


if _trace_path:
    _install(_trace_path)

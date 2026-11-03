#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Count the SQL statements of a WeeWX process, and freeze its clock.

weewx-bench copies this module into the site-packages of every benchmarked WeeWX
installation, together with a .pth file that imports it at interpreter start. It
does nothing unless one of these environment variables is set:

WEEWX_BENCH_TRACE names a file. Every SQL statement is counted by its first
keyword, and the counts are written to that file, as JSON, when the interpreter
exits. The hooks sit in the database drivers, sqlite3 and PyMySQL, not in weedb.
That way they work for every WeeWX version, whatever weedb looks like inside.

WEEWX_BENCH_FREEZE is a unix epoch time. The clock stands still at that time, for
time.time(), datetime.datetime.now() and everything else that asks for the current
time. Skins put the current time into their pages, and without this, two runs of
the same version would never write the same files.

WEEWX_BENCH_CLOCK is a unix epoch time. The clock starts at that time and runs on
from there. weewxd primes its first archive period from the clock, and a gateway
that pushes readings from two years ago needs it to start back then.

WEEWX_BENCH_NOSYNC, if set, turns off the fsync of SQLite. While weewxd catches up
on two years, it commits every archive record on its own, and the fsync of each
commit takes most of the time. The data written is the same.

All of these slow the process down or change what it measures, so a run with any of
them is never a timed run.
"""

from __future__ import annotations

import os


def _trace(trace_path: str) -> None:
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


def _nosync() -> None:
    import sqlite3

    original_connect = sqlite3.connect

    def connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        connection = original_connect(*args, **kwargs)
        connection.execute('PRAGMA synchronous = OFF')
        return connection

    sqlite3.connect = connect


def _freeze(epoch: str) -> None:
    import time_machine

    time_machine.travel(float(epoch), tick=False).start()


def _clock(epoch: str) -> None:
    import time_machine

    # weewxd waits for "sane time": the clock has to be later than its own file.
    # weewx takes the time it compares with when it is imported, so it is imported
    # while the clock is still right.
    import weewx  # noqa: F401

    time_machine.travel(float(epoch), tick=True).start()


if os.environ.get('WEEWX_BENCH_TRACE'):
    _trace(os.environ['WEEWX_BENCH_TRACE'])
if os.environ.get('WEEWX_BENCH_NOSYNC'):
    _nosync()
if os.environ.get('WEEWX_BENCH_FREEZE'):
    _freeze(os.environ['WEEWX_BENCH_FREEZE'])
if os.environ.get('WEEWX_BENCH_CLOCK'):
    _clock(os.environ['WEEWX_BENCH_CLOCK'])

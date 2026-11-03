#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Write the benchmark data set into a WeeWX database.

This script runs under the Python of a benchmarked WeeWX installation and imports
that installation's ``weewx``. It must not import ``weewx_bench``.

Usage::

    python generate.py DATABASE_JSON START_TS STOP_TS INTERVAL

``DATABASE_JSON`` is a WeeWX database dictionary, the same keys a ``[[Databases]]``
entry has after ``[DatabaseTypes]`` was merged in. ``INTERVAL`` is in seconds.

Every value is a function of the timestamp alone. The same arguments give the same
database, on every machine and with every WeeWX version.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Iterator

import weedb
import weewx
import weewx.manager
import weewx.schemas.wview_extended
import weewx.wxformulas

DAY = 86400
YEAR = 365.25 * DAY


def wave(timestamp: int, period: float, phase: float = 0.0) -> float:
    """Return a sine of the given period, in seconds, between -1 and 1."""
    return math.sin(2.0 * math.pi * timestamp / period + phase)


def make_record(timestamp: int, interval: int) -> dict:
    """Return the archive record for one timestamp, in US units."""
    # The station is on the prime meridian and the runs use TZ=UTC, so noon is at
    # 12:00 UTC. day_angle is 0 at midnight; -cos(day_angle) peaks at noon.
    day_angle = 2.0 * math.pi * (timestamp % DAY) / DAY
    year_angle = 2.0 * math.pi * (timestamp % YEAR) / YEAR

    out_temp = (
        50.0
        - 20.0 * math.cos(year_angle - 0.35)
        - 8.0 * math.cos(day_angle - 0.8)
        + 3.0 * wave(timestamp, 5.3 * DAY)
    )
    out_humidity = min(
        100.0, max(5.0, 70.0 + 20.0 * math.cos(day_angle - 0.8) + 8.0 * wave(timestamp, 3.1 * DAY))
    )
    wind_speed = max(0.0, 6.0 + 4.0 * wave(timestamp, 2.9 * DAY) - 2.0 * math.cos(day_angle))
    wind_dir = (180.0 + 150.0 * wave(timestamp, 6.1 * DAY)) % 360.0
    barometer = 30.0 + 0.4 * wave(timestamp, 4.3 * DAY) + 0.05 * math.sin(2.0 * day_angle)

    # It rains whenever this slow wave is above 0.97: a few wet spells per month,
    # about two inches in total, so the rain plots and totals are not empty.
    spell = wave(timestamp, 3.7 * DAY)
    if spell > 0.99:
        rain = 0.004
    elif spell > 0.97:
        rain = 0.002
    else:
        rain = 0.0

    season = 0.6 - 0.4 * math.cos(year_angle - 0.2)
    radiation = max(0.0, -math.cos(day_angle)) * 1000.0 * season

    record = {
        'dateTime': timestamp,
        'usUnits': weewx.US,
        'interval': interval // 60,
        'outTemp': out_temp,
        'outHumidity': out_humidity,
        'dewpoint': weewx.wxformulas.dewpointF(out_temp, out_humidity),
        'windchill': weewx.wxformulas.windchillF(out_temp, wind_speed),
        'heatindex': weewx.wxformulas.heatindexF(out_temp, out_humidity),
        'humidex': weewx.wxformulas.humidexF(out_temp, out_humidity),
        'appTemp': weewx.wxformulas.apptempF(out_temp, out_humidity, wind_speed),
        'barometer': barometer,
        'pressure': barometer - 0.9,
        'altimeter': barometer + 0.01,
        'windSpeed': wind_speed,
        'windDir': wind_dir,
        'windGust': 1.5 * wind_speed + 1.0,
        'windGustDir': wind_dir,
        'rain': rain,
        'rainRate': rain * 3600.0 / interval,
        'radiation': radiation,
        'UV': radiation / 90.0,
        'ET': 0.0002 * radiation * interval / 3600.0,
        'inTemp': 70.0 + 2.0 * math.sin(day_angle),
        'inHumidity': 40.0 + 5.0 * math.sin(year_angle),
        'extraTemp1': out_temp - 3.0,
        'extraHumid1': min(100.0, out_humidity + 5.0),
        'rxCheckPercent': min(100.0, 98.0 + 2.0 * wave(timestamp, 0.7 * DAY)),
        'consBatteryVoltage': 4.6 - 0.1 * (timestamp % (30 * DAY)) / (30 * DAY),
    }
    return record


def generate(start_ts: int, stop_ts: int, interval: int) -> Iterator[dict]:
    """Yield the records from start_ts to stop_ts, both included."""
    for timestamp in range(start_ts, stop_ts + 1, interval):
        yield make_record(timestamp, interval)


def is_complete(database_dict: dict, start_ts: int, stop_ts: int) -> bool:
    """Return True if the database holds exactly this data set, daily summaries included."""
    try:
        with weewx.manager.DaySummaryManager.open(database_dict) as manager:
            # The backfill writes 'lastUpdate' last. A run that was interrupted while
            # backfilling has all archive records, but not this value.
            return bool(
                manager.firstGoodStamp() == start_ts
                and manager.lastGoodStamp() == stop_ts
                and manager._read_metadata('lastUpdate') == str(stop_ts)
            )
    except (weedb.OperationalError, weedb.ProgrammingError):
        # No database, or no tables in it.
        return False


def write_archive(manager: weewx.manager.Manager, records: Iterator[dict]) -> None:
    """Insert the records into the archive table without touching the daily summaries.

    Manager.addRecord() would update the daily summaries record by record, which is
    about 30 times slower than one backfill afterwards.
    """
    first = next(records)
    columns = list(first)
    sql = 'INSERT INTO %s (%s) VALUES (%s)' % (
        manager.table_name,
        ', '.join(columns),
        ', '.join('?' * len(columns)),
    )
    pending = [first]
    for record in records:
        pending.append(record)
        if len(pending) == 5000:
            _insert(manager, sql, columns, pending)
            pending = []
    _insert(manager, sql, columns, pending)


def _insert(manager: weewx.manager.Manager, sql: str, columns: list, records: list) -> None:
    with weedb.Transaction(manager.connection) as cursor:
        for record in records:
            # weedb.mysql rewrites the '?' placeholders for MySQL.
            cursor.execute(sql, tuple(record[column] for column in columns))


def main() -> None:
    database_dict = json.loads(sys.argv[1])
    start_ts, stop_ts, interval = (int(argument) for argument in sys.argv[2:5])

    if is_complete(database_dict, start_ts, stop_ts):
        print('Data set is already present.')
        return

    # Whatever is there belongs to another data set or to an interrupted run. The
    # database is owned by weewx-bench, so dropping it loses nothing.
    try:
        weedb.drop(database_dict)
    except weedb.NoDatabaseError:
        pass

    schema = weewx.schemas.wview_extended.schema
    with weewx.manager.DaySummaryManager.open_with_create(database_dict, schema=schema) as manager:
        write_archive(manager, generate(start_ts, stop_ts, interval))
    # Open again: the manager caches the first and last timestamp, and the backfill
    # needs the ones that are now in the archive table.
    with weewx.manager.DaySummaryManager.open(database_dict) as manager:
        manager.backfill_day_summary(progress_fn=lambda *args: None)
    print('Wrote %d records.' % ((stop_ts - start_ts) // interval + 1))


if __name__ == '__main__':
    main()

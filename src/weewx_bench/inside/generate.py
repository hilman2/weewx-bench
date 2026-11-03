#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Write the benchmark data set into a WeeWX database.

This script runs under the Python of a benchmarked WeeWX installation and imports
that installation's ``weewx``. It must not import ``weewx_bench``.

Usage::

    python generate.py DATABASE_JSON PROFILE START_TS STOP_TS INTERVAL

``DATABASE_JSON`` is a WeeWX database dictionary, the same keys a ``[[Databases]]``
entry has after ``[DatabaseTypes]`` was merged in. ``PROFILE`` is one of the keys of
PROFILES. ``INTERVAL`` is in seconds.

Every value is a function of the timestamp alone. The same arguments give the same
database, on every machine and with every WeeWX version.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable, Iterator

import weedb
import weewx
import weewx.manager
import weewx.units
import weewx.wxformulas

try:
    import weewx.schemas.wview_extended as wview_extended
except ImportError:
    # WeeWX 5.0 and 5.1 have the schemas as a top-level package.
    import schemas.wview_extended as wview_extended

DAY = 86400
YEAR = 365.25 * DAY
# The station altitude that weewx-bench gives `weectl station create`.
ALTITUDE_FT = 984.0


def wave(timestamp: int, period: float, phase: float = 0.0) -> float:
    """Return a sine of the given period, in seconds, between -1 and 1."""
    return math.sin(2.0 * math.pi * timestamp / period + phase)


def outdoor(timestamp: int, interval: int) -> dict:
    """Return the outdoor weather at one timestamp, in US units.

    Both profiles share this weather. They differ in the sensors that see it.
    """
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

    return {
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
        'cloudbase': weewx.wxformulas.cloudbase_US(out_temp, out_humidity, ALTITUDE_FT),
        'barometer': barometer,
        'pressure': barometer - 0.9,
        'altimeter': barometer + 0.01,
        'windSpeed': wind_speed,
        'windDir': wind_dir,
        'windGust': 1.5 * wind_speed + 1.0,
        'windrun': wind_speed * interval / 3600.0,
        'rain': rain,
        'rainRate': rain * 3600.0 / interval,
        'radiation': radiation,
        # Thunderstorms come with the heaviest rain, in summer only.
        'thunder': spell > 0.995 and season > 0.8,
    }


def davis(timestamp: int, interval: int) -> dict:
    """Return a record as a Davis Vantage Pro2 Plus with a soil and leaf station writes it.

    US units, as the Vantage driver uses them.
    """
    record = outdoor(timestamp, interval)
    del record['thunder']
    day_angle = 2.0 * math.pi * (timestamp % DAY) / DAY
    record.update(
        {
            'windGustDir': record['windDir'],
            'UV': record['radiation'] / 90.0,
            'ET': 0.0002 * record['radiation'] * interval / 3600.0,
            'inTemp': 70.0 + 2.0 * math.sin(day_angle),
            'inHumidity': 40.0 + 5.0 * wave(timestamp, YEAR),
            'soilTemp1': record['outTemp'] * 0.3 + 38.0,
            'soilMoist1': 20.0 + 15.0 * wave(timestamp, 9.0 * DAY),
            'leafTemp1': record['outTemp'] - 1.0,
            'leafWet1': 15.0 if record['rain'] else 0.0,
            'rxCheckPercent': min(100.0, 98.0 + 2.0 * wave(timestamp, 0.7 * DAY)),
            'consBatteryVoltage': 4.6 - 0.1 * (timestamp % (30 * DAY)) / (30 * DAY),
            'txBatteryStatus': 0.0,
        }
    )
    return record


def ecowitt(timestamp: int, interval: int) -> dict:
    """Return a record as an Ecowitt gateway pushes it, after the driver mapped it.

    The sensors are a WS90 outdoor array, two WH31 thermo-hygrometers, a WH45 air
    quality sensor, a WH57 lightning detector, two WH51 soil moisture sensors and a
    WN34 soil thermometer. The values are in US units here and stored in METRICWX.
    """
    record = outdoor(timestamp, interval)
    thunder = record.pop('thunder')
    day_angle = 2.0 * math.pi * (timestamp % DAY) / DAY
    pm2_5 = 8.0 + 6.0 * wave(timestamp, 2.3 * DAY) + 3.0 * math.sin(day_angle)
    record.update(
        {
            # The WS90 reports the UV index in whole numbers.
            'UV': float(round(record['radiation'] / 90.0)),
            'inTemp': 71.0 + 1.5 * math.sin(day_angle),
            'inHumidity': 45.0 + 5.0 * wave(timestamp, YEAR),
            'extraTemp1': 64.0 + 2.0 * math.sin(day_angle),
            'extraHumid1': 55.0 + 5.0 * wave(timestamp, 4.0 * DAY),
            'extraTemp2': record['outTemp'] * 0.2 + 52.0,
            'extraHumid2': 70.0 + 10.0 * wave(timestamp, 6.0 * DAY),
            'pm2_5': pm2_5,
            'pm10_0': 1.4 * pm2_5,
            'co2': 450.0 + 300.0 * max(0.0, math.sin(day_angle)),
            # The WH57 reports zero strikes, and no distance, when there is no storm.
            'lightning_strike_count': 12.0 if thunder else 0.0,
            'lightning_distance': 5.0 + 4.0 * wave(timestamp, 0.1 * DAY) if thunder else None,
            'soilMoist1': 25.0 + 15.0 * wave(timestamp, 9.0 * DAY),
            'soilMoist2': 35.0 + 10.0 * wave(timestamp, 11.0 * DAY),
            'soilTemp1': record['outTemp'] * 0.3 + 38.0,
            'outTempBatteryStatus': 0.0,
            'batteryStatus1': 0.0,
            'batteryStatus2': 0.0,
        }
    )
    metric: dict = weewx.units.to_METRICWX(record)
    return metric


PROFILES: dict[str, Callable[[int, int], dict]] = {'davis': davis, 'ecowitt': ecowitt}


def make_record(profile: str, timestamp: int, interval: int) -> dict:
    """Return the archive record of a profile for one timestamp."""
    record = PROFILES[profile](timestamp, interval)
    # math.sin() and friends may differ in the last bit between C libraries, e.g.
    # glibc and macOS. Three decimals hide that, so the database is the same on every
    # machine. That is also about the resolution a real station reports.
    return {
        key: round(value, 3) if isinstance(value, float) else value for key, value in record.items()
    }


def generate(profile: str, start_ts: int, stop_ts: int, interval: int) -> Iterator[dict]:
    """Yield the records from start_ts to stop_ts, both included."""
    for timestamp in range(start_ts, stop_ts + 1, interval):
        yield make_record(profile, timestamp, interval)


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
    unknown = set(columns) - set(manager.sqlkeys)
    if unknown:
        raise SystemExit('Not in the database schema: %s' % ', '.join(sorted(unknown)))
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
    profile = sys.argv[2]
    start_ts, stop_ts, interval = (int(argument) for argument in sys.argv[3:6])

    if is_complete(database_dict, start_ts, stop_ts):
        print('Data set is already present.')
        return

    # Whatever is there belongs to another data set or to an interrupted run. The
    # database is owned by weewx-bench, so dropping it loses nothing.
    try:
        weedb.drop(database_dict)
    except weedb.NoDatabaseError:
        pass

    schema = wview_extended.schema
    with weewx.manager.DaySummaryManager.open_with_create(database_dict, schema=schema) as manager:
        write_archive(manager, generate(profile, start_ts, stop_ts, interval))
    # Open again: the manager caches the first and last timestamp, and the backfill
    # needs the ones that are now in the archive table.
    with weewx.manager.DaySummaryManager.open(database_dict) as manager:
        manager.backfill_day_summary(progress_fn=lambda *args: None)
    print('Wrote %d records.' % ((stop_ts - start_ts) // interval + 1))


if __name__ == '__main__':
    main()

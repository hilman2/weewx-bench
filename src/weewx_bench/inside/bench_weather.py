#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""The weather of the benchmark, and what the sensors of each station make of it.

Every value is a function of the timestamp alone. This module imports nothing from
WeeWX: generate.py uses it under the Python of a WeeWX installation, and the Vantage
console and the Ecowitt gateway of weewx-bench use it under the Python of weewx-bench.

All values are in US units: degree_F, percent, mile_per_hour, inHg, inch, W/m².
"""

from __future__ import annotations

import math

DAY = 86400
YEAR = 365.25 * DAY


def wave(timestamp: int, period: float, phase: float = 0.0) -> float:
    """Return a sine of the given period, in seconds, between -1 and 1."""
    return math.sin(2.0 * math.pi * timestamp / period + phase)


def _day_angle(timestamp: int) -> float:
    # The station is on the prime meridian and the runs use TZ=UTC, so noon is at
    # 12:00 UTC. The angle is 0 at midnight; -cos() of it peaks at noon.
    return 2.0 * math.pi * (timestamp % DAY) / DAY


def outdoor(timestamp: int) -> dict:
    """Return the outdoor weather at one timestamp.

    'rain' is the amount of the five minutes up to the timestamp. 'thunder' is True
    while a thunderstorm is overhead.
    """
    day_angle = _day_angle(timestamp)
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
        'outTemp': out_temp,
        'outHumidity': out_humidity,
        'windSpeed': wind_speed,
        'windDir': wind_dir,
        'windGust': 1.5 * wind_speed + 1.0,
        'barometer': barometer,
        'rain': rain,
        'radiation': radiation,
        # Thunderstorms come with the heaviest rain, in summer only.
        'thunder': spell > 0.995 and season > 0.8,
    }


def davis(timestamp: int, weather: dict, interval: int) -> dict:
    """Return the readings of the other sensors of a Vantage Pro2 Plus.

    The station has a soil and leaf station besides the ISS. weather is what outdoor()
    returned for the same timestamp.
    """
    day_angle = _day_angle(timestamp)
    return {
        'UV': weather['radiation'] / 90.0,
        'ET': 0.0002 * weather['radiation'] * interval / 3600.0,
        'inTemp': 70.0 + 2.0 * math.sin(day_angle),
        'inHumidity': 40.0 + 5.0 * wave(timestamp, YEAR),
        'soilTemp1': weather['outTemp'] * 0.3 + 38.0,
        'soilMoist1': 20.0 + 15.0 * wave(timestamp, 9.0 * DAY),
        'leafTemp1': weather['outTemp'] - 1.0,
        'leafWet1': 15.0 if weather['rain'] else 0.0,
        'rxCheckPercent': min(100.0, 98.0 + 2.0 * wave(timestamp, 0.7 * DAY)),
        'consBatteryVoltage': 4.6 - 0.1 * (timestamp % (30 * DAY)) / (30 * DAY),
    }


def ecowitt(timestamp: int, weather: dict) -> dict:
    """Return the readings of the other sensors of an Ecowitt station.

    The sensors are a WS90 outdoor array, two WH31 thermo-hygrometers, a WH45 air
    quality sensor, a WH57 lightning detector, two WH51 soil moisture sensors and a
    WN34 soil thermometer. weather is what outdoor() returned for the same timestamp.
    """
    day_angle = _day_angle(timestamp)
    pm2_5 = 8.0 + 6.0 * wave(timestamp, 2.3 * DAY) + 3.0 * math.sin(day_angle)
    thunder = weather['thunder']
    return {
        # The WS90 reports the UV index in whole numbers.
        'UV': float(round(weather['radiation'] / 90.0)),
        'inTemp': 71.0 + 1.5 * math.sin(day_angle),
        'inHumidity': 45.0 + 5.0 * wave(timestamp, YEAR),
        'extraTemp1': 64.0 + 2.0 * math.sin(day_angle),
        'extraHumid1': 55.0 + 5.0 * wave(timestamp, 4.0 * DAY),
        'extraTemp2': weather['outTemp'] * 0.2 + 52.0,
        'extraHumid2': 70.0 + 10.0 * wave(timestamp, 6.0 * DAY),
        'pm2_5': pm2_5,
        'pm10_0': 1.4 * pm2_5,
        'co2': 450.0 + 300.0 * max(0.0, math.sin(day_angle)),
        # The WH57 counts the strikes of a five-minute storm as twelve.
        'lightning_strike_count': 12.0 if thunder else 0.0,
        'lightning_distance': 5.0 + 4.0 * wave(timestamp, 0.1 * DAY) if thunder else None,
        'soilMoist1': 25.0 + 15.0 * wave(timestamp, 9.0 * DAY),
        'soilMoist2': 35.0 + 10.0 * wave(timestamp, 11.0 * DAY),
        'soilTemp1': weather['outTemp'] * 0.3 + 38.0,
    }

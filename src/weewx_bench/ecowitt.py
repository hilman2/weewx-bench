#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""An Ecowitt gateway that pushes its readings, the way the "Customized" upload does.

The gateway is a GW2000 with a WS69 outdoor array, two WH31 thermo-hygrometers, a
WH45 air quality sensor, a WH57 lightning detector, two WH51 soil moisture sensors
and a WN34 soil thermometer. It uploads every five minutes, with dateutc set to the
time of the reading, so WeeWX files every upload under the time it belongs to.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator

import weewx_bench.inside.bench_weather as bench_weather

INTERVAL = 300
PASSKEY = 'BENCH0000000000000000000000000000'
DAY = 86400


def uploads(start_ts: int, stop_ts: int) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (timestamp, form) for every upload after start_ts up to stop_ts.

    The rain gauge and the lightning detector report daily totals, which the gateway
    resets at midnight. The driver and WeeWX turn them into amounts per record.
    """
    day_rain = 0.0
    year_rain = 0.0
    total_rain = 0.0
    strikes_today = 0
    last_strike = None
    day = None
    year = None
    for timestamp in range(start_ts + INTERVAL, stop_ts + 1, INTERVAL):
        weather = bench_weather.outdoor(timestamp)
        sensors = bench_weather.ecowitt(timestamp, weather)
        # The reading at 00:00 still belongs to the day before.
        if (timestamp - 1) // DAY != day:
            day = (timestamp - 1) // DAY
            day_rain = 0.0
            strikes_today = 0
        if time.gmtime(timestamp - 1).tm_year != year:
            year = time.gmtime(timestamp - 1).tm_year
            year_rain = 0.0
        day_rain += weather['rain']
        year_rain += weather['rain']
        total_rain += weather['rain']
        if weather['thunder']:
            strikes_today += int(sensors['lightning_strike_count'])
            last_strike = timestamp
        form = {
            'PASSKEY': PASSKEY,
            'stationtype': 'GW2000A_V3.1.4',
            'runtime': str(timestamp - start_ts),
            'dateutc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(timestamp)),
            'tempinf': '%.1f' % sensors['inTemp'],
            'humidityin': '%d' % round(sensors['inHumidity']),
            'baromrelin': '%.3f' % weather['barometer'],
            'baromabsin': '%.3f' % (weather['barometer'] - 0.9),
            'tempf': '%.1f' % weather['outTemp'],
            'humidity': '%d' % round(weather['outHumidity']),
            'winddir': '%d' % round(weather['windDir']),
            'windspeedmph': '%.2f' % weather['windSpeed'],
            'windgustmph': '%.2f' % weather['windGust'],
            'maxdailygust': '%.2f' % weather['windGust'],
            'solarradiation': '%.2f' % weather['radiation'],
            'uv': '%d' % sensors['UV'],
            'rainratein': '%.3f' % (weather['rain'] * 3600 / INTERVAL),
            'eventrainin': '%.3f' % day_rain,
            'hourlyrainin': '%.3f' % weather['rain'],
            'dailyrainin': '%.3f' % day_rain,
            'weeklyrainin': '%.3f' % day_rain,
            'monthlyrainin': '%.3f' % day_rain,
            'yearlyrainin': '%.3f' % year_rain,
            'totalrainin': '%.3f' % total_rain,
            'temp1f': '%.1f' % sensors['extraTemp1'],
            'humidity1': '%d' % round(sensors['extraHumid1']),
            'temp2f': '%.1f' % sensors['extraTemp2'],
            'humidity2': '%d' % round(sensors['extraHumid2']),
            'soilmoisture1': '%d' % round(sensors['soilMoist1']),
            'soilmoisture2': '%d' % round(sensors['soilMoist2']),
            'tf_ch1': '%.1f' % sensors['soilTemp1'],
            'tf_co2': '%.1f' % sensors['inTemp'],
            'humi_co2': '%d' % round(sensors['inHumidity']),
            'pm25_co2': '%.1f' % sensors['pm2_5'],
            'pm25_24h_co2': '%.1f' % sensors['pm2_5'],
            'pm10_co2': '%.1f' % sensors['pm10_0'],
            'pm10_24h_co2': '%.1f' % sensors['pm10_0'],
            'co2': '%d' % round(sensors['co2']),
            'co2_24h': '%d' % round(sensors['co2']),
            'lightning_num': '%d' % strikes_today,
            # The WH57 reports the distance in km, and keeps the last one.
            'lightning': (
                '%.1f' % (sensors['lightning_distance'] * 1.609344)
                if sensors['lightning_distance'] is not None
                else ''
            ),
            'lightning_time': str(last_strike) if last_strike else '',
            'wh65batt': '0',
            'batt1': '0',
            'batt2': '0',
            'soilbatt1': '1.5',
            'soilbatt2': '1.5',
            'tf_batt1': '1.5',
            'co2_batt': '6',
            'wh57batt': '5',
            'freq': '868M',
            'model': 'GW2000A',
            'interval': str(INTERVAL),
        }
        yield timestamp, form


class Gateway(threading.Thread):
    """Push the uploads to a URL, never more than ahead uploads ahead of the archive.

    The driver keeps uploads in a queue of limited size and answers 503 when it is
    full. progress() returns the timestamp of the last archive record WeeWX wrote, or
    None, and the gateway waits for it.
    """

    def __init__(
        self,
        url: str,
        forms: Iterator[tuple[int, dict[str, str]]],
        progress: Callable[[], int | None],
        ahead: int,
    ) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.forms = forms
        self.progress = progress
        self.ahead = ahead
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            for index, (timestamp, form) in enumerate(self.forms):
                while True:
                    archived = self.progress()
                    if index < self.ahead or (
                        archived is not None and timestamp - archived <= self.ahead * INTERVAL
                    ):
                        break
                    time.sleep(0.02)
                data = urllib.parse.urlencode(form).encode()
                self._send(data)
        except Exception as error:  # noqa: BLE001 - reported by the caller
            self.error = error

    def _send(self, data: bytes) -> None:
        """Send one upload. Try again while the queue of the driver is full."""
        for _ in range(1000):
            try:
                with urllib.request.urlopen(self.url, data=data, timeout=30) as response:
                    response.read()
                return
            except urllib.error.HTTPError as error:
                if error.code != 503:
                    raise
                time.sleep(0.05)
        raise RuntimeError('The driver kept its queue full for a minute')

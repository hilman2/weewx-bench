#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""A Davis Vantage console on a TCP port, as a WeatherLinkIP makes it reachable.

The Vantage driver that ships with WeeWX connects to it with ``type = ethernet``. At
startup, WeeWX asks the console for every archive record it has not stored yet
(DMPAFT). This console holds the whole data set in its logger, so WeeWX reads two
years of records through its own driver and engine, without waiting for real time.

Only the commands the driver sends at startup and during the catch-up are answered.
The byte layouts follow the Vantage Serial Protocol document, version 2.6.1.
"""

from __future__ import annotations

import binascii
import calendar
import socket
import struct
import threading
import time
from collections.abc import Iterator

import weewx_bench.inside.bench_weather as bench_weather

ACK = b'\x06'
INTERVAL = 300
ALTITUDE_FT = 984
ISS_ID = 1

# Revision B archive record, 52 bytes, little-endian. The field names are those of
# rec_B_schema in weewx/drivers/vantage.py.
RECORD_B = struct.Struct('<HHhhhHHHHHhBBBBBBBBHBBBBBBBBBBBBBBBBBBBB')
assert RECORD_B.size == 52

# EEPROM addresses the driver reads in Vantage._setup(), with the bytes it gets.
# Unit bits 0: inHg, degree_F, foot, inch, mile_per_hour. Setup bits 0x08: large wind
# cups, 0.01 inch rain bucket.
EEPROM = {
    0x29: bytes([0x00]),
    0x2B: bytes([0x08]),
    0x2C: bytes([1]),
    0x2D: bytes([INTERVAL // 60]),
    0x0F: struct.pack('<h', ALTITUDE_FT),
}
HARDWARE_TYPE = 16  # Vantage Pro2


def crc16(data: bytes) -> bytes:
    """Return the CRC the console appends to binary data, big-endian.

    It is CRC-CCITT with a start value of 0, which is what binascii.crc_hqx computes.
    """
    return struct.pack('>H', binascii.crc_hqx(data, 0))


def _sector(degrees: float) -> int:
    return round(degrees / 22.5) % 16


def archive_records(start_ts: int, stop_ts: int) -> Iterator[tuple[int, bytes]]:
    """Yield (timestamp, record) for every archive record after start_ts up to stop_ts.

    The values come from bench_weather, quantized as the console stores them. Rain is
    counted in tips of a 0.01 inch bucket. The fractions of a tip carry over from one
    record to the next, so the total over the data set matches the model.
    """
    rain_total = 0.0
    tips_so_far = 0
    previous_temp = bench_weather.outdoor(start_ts)['outTemp']
    for timestamp in range(start_ts + INTERVAL, stop_ts + 1, INTERVAL):
        weather = bench_weather.outdoor(timestamp)
        sensors = bench_weather.davis(timestamp, weather, INTERVAL)
        rain_total += weather['rain']
        tips = int(rain_total * 100.0 + 1e-9) - tips_so_far
        tips_so_far += tips
        tt = time.gmtime(timestamp)
        expected_samples = 960.0 * (INTERVAL // 60) / (41 + ISS_ID - 1)
        temp = weather['outTemp']
        uv = round(sensors['UV'] * 10)
        record = RECORD_B.pack(
            tt.tm_mday + (tt.tm_mon << 5) + ((tt.tm_year - 2000) << 9),
            tt.tm_hour * 100 + tt.tm_min,
            round(temp * 10),
            round(max(temp, previous_temp) * 10),
            round(min(temp, previous_temp) * 10),
            tips,
            tips * 3600 // INTERVAL,
            round(weather['barometer'] * 1000),
            round(weather['radiation']),
            round(sensors['rxCheckPercent'] / 100.0 * expected_samples),
            round(sensors['inTemp'] * 10),
            round(sensors['inHumidity']),
            round(weather['outHumidity']),
            round(weather['windSpeed']),
            round(weather['windGust']),
            _sector(weather['windDir']),
            _sector(weather['windDir']),
            uv,
            round(sensors['ET'] * 1000),
            round(weather['radiation']),
            uv,
            193,  # no forecast rule
            round(sensors['leafTemp1']) + 90,
            0xFF,
            round(sensors['leafWet1']),
            0xFF,
            round(sensors['soilTemp1']) + 90,
            0xFF,
            0xFF,
            0xFF,
            0x00,  # revision B
            0xFF,
            0xFF,
            0xFF,
            0xFF,
            0xFF,
            round(sensors['soilMoist1']),
            0xFF,
            0xFF,
            0xFF,
        )
        previous_temp = temp
        yield timestamp, record


class Console(threading.Thread):
    """A Vantage console that serves archive records on a TCP port.

    The port is chosen by the operating system and is in port once the object
    exists. The thread serves one connection after another until stop() is called.
    """

    def __init__(self, records: list[tuple[int, bytes]]) -> None:
        super().__init__(daemon=True)
        self.records = records
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port: int = self.server.getsockname()[1]
        self.stopping = False

    def stop(self) -> None:
        self.stopping = True
        self.server.close()

    def run(self) -> None:
        while not self.stopping:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                try:
                    self._serve(connection)
                except OSError:
                    pass

    def _serve(self, connection: socket.socket) -> None:
        buffer = b''
        pages: list[bytes] = []
        awaiting_date = False
        while True:
            data = connection.recv(4096)
            if not data:
                return
            buffer += data
            while buffer:
                if awaiting_date:
                    # DMPAFT: four bytes of date and time, then their CRC.
                    if len(buffer) < 6:
                        break
                    request, buffer = buffer[:6], buffer[6:]
                    awaiting_date = False
                    pages = self._pages_after(request[:4])
                    connection.sendall(ACK)
                    header = struct.pack('<HH', len(pages), 0)
                    connection.sendall(header + crc16(header))
                elif pages and buffer[:1] == ACK:
                    # The driver acknowledges each page by asking for the next.
                    buffer = buffer[1:]
                    connection.sendall(pages.pop(0))
                elif b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    pages = []
                    awaiting_date = self._command(connection, line.strip(b'\r'))
                else:
                    break

    def _command(self, connection: socket.socket, line: bytes) -> bool:
        """Answer one line. Return True if the console now waits for a DMPAFT date."""
        if line == b'':
            # A wake-up call.
            connection.sendall(b'\n\r')
        elif line == b'WRD\x12\x4d':
            connection.sendall(ACK + bytes([HARDWARE_TYPE]))
        elif line.startswith(b'EEBRD'):
            address, length = (int(word, 16) for word in line.split()[1:3])
            value = EEPROM.get(address, bytes(length))[:length]
            connection.sendall(ACK + value + crc16(value))
        elif line == b'GETTIME':
            tt = time.gmtime()
            value = struct.pack(
                '<bbbbbb',
                tt.tm_sec,
                tt.tm_min,
                tt.tm_hour,
                tt.tm_mday,
                tt.tm_mon,
                tt.tm_year - 1900,
            )
            connection.sendall(ACK + value + crc16(value))
        elif line == b'DMPAFT':
            connection.sendall(ACK)
            return True
        else:
            # LOOP, LPS and the rest: acknowledged, and nothing follows. WeeWX is
            # stopped once the catch-up is complete, so it never needs LOOP data.
            connection.sendall(ACK)
        return False

    def _pages_after(self, request: bytes) -> list[bytes]:
        """Return the pages of all records after the date and time in a DMPAFT request."""
        date_stamp, time_stamp = struct.unpack('<HH', request)
        if date_stamp == 0:
            since = 0
        else:
            since = calendar.timegm(
                (
                    (date_stamp >> 9) + 2000,
                    (date_stamp >> 5) & 0x0F,
                    date_stamp & 0x1F,
                    time_stamp // 100,
                    time_stamp % 100,
                    0,
                )
            )
        records = [record for timestamp, record in self.records if timestamp > since]
        pages = []
        for number, first in enumerate(range(0, len(records), 5)):
            chunk = records[first : first + 5]
            # A record that starts with four 0xFF bytes is unused, and ends the dump.
            chunk += [b'\xff' * 52] * (5 - len(chunk))
            body = bytes([number % 256]) + b''.join(chunk) + b'\x00' * 4
            pages.append(body + crc16(body))
        if len(records) % 5 == 0:
            # The driver reads until it meets an unused record. Without one, it would
            # take the last full page as the end only by the page count.
            body = bytes([len(pages) % 256]) + b'\xff' * 260 + b'\x00' * 4
            pages.append(body + crc16(body))
        return pages

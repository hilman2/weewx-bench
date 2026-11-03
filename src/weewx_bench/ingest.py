#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Fill a station's database through a real driver and weewxd.

Each WeeWX version reads the data set through its own driver and engine: StdConvert,
StdQC, StdWXCalculate with its xtypes, StdArchive with the daily summaries. Two
versions that handle the same readings differently end up with different databases,
and check compares them.

davis: the Vantage driver of WeeWX reads a Vantage console of weewx-bench over TCP,
in one catch-up at startup.

ecowitt: the ecowittcustom driver receives the uploads of an Ecowitt gateway of
weewx-bench over HTTP. The clock of weewxd starts at the beginning of the data set,
and the timestamps of the uploads move the archive periods on from there.
"""

from __future__ import annotations

import os
import pathlib
import signal
import socket
import sqlite3
import subprocess
import time
from typing import Any

import configobj

import weewx_bench.dataset
import weewx_bench.ecowitt
import weewx_bench.installs
import weewx_bench.measure
import weewx_bench.skins
import weewx_bench.vantage

ECOWITTCUSTOM_URL = (
    'https://github.com/weewx-packages/weewx-ecowittcustom/releases/download/v0.3.0/'
    'weewx-ecowittcustom-0.3.0.tar.gz'
)
# The driver holds up to 128 uploads and answers 503 beyond that. The gateway stays
# well below.
AHEAD = 64
# How long weewxd may take for the whole data set before it counts as hung.
TIMEOUT = 3 * 3600


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port: int = probe.getsockname()[1]
        return port


def _configure(target: weewx_bench.installs.Install, profile: str, port: int) -> None:
    """Point the station at the driver, and leave out what does not handle data."""
    config = configobj.ConfigObj(str(target.config_path), encoding='utf-8')
    services = config['Engine']['Services']
    # No clock synchronisation, no uploads and no reports while the data comes in.
    # The reports run afterwards, with `weectl report run`.
    services['prep_services'] = ''
    services['restful_services'] = ''
    services['report_services'] = ''
    if profile == 'davis':
        config['Station']['station_type'] = 'Vantage'
        config['Vantage'] = {
            'driver': 'weewx.drivers.vantage',
            'type': 'ethernet',
            'host': '127.0.0.1',
            'tcp_port': str(port),
            # A real WeatherLinkIP needs a pause after each write. This console does
            # not, and with the default of half a second, two years of pages would
            # take hours.
            'tcp_send_delay': '0',
            'command_delay': '0.05',
            'wait_before_retry': '0.2',
            'iss_id': str(weewx_bench.vantage.ISS_ID),
            'model_type': '2',
        }
    else:
        config['Station']['station_type'] = 'Ecowittcustom'
        config['Ecowittcustom'].update({'address': '127.0.0.1', 'port': str(port)})
        # Ecowitt drivers are usually set up to store metric data, so StdConvert has
        # work to do.
        config['StdConvert']['target_unit'] = 'METRICWX'
    config.write()


def _last_record(database: pathlib.Path) -> int | None:
    """Return the timestamp of the last archive record, or None if there is none yet."""
    try:
        with sqlite3.connect('file:%s?mode=ro' % database, uri=True, timeout=5) as connection:
            row = connection.execute('SELECT MAX(dateTime) FROM archive').fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row and row[0] is not None else None


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError('The driver did not open port %d' % port)


def ingest(
    target: weewx_bench.installs.Install,
    data_set: weewx_bench.dataset.DataSet,
    work_dir: pathlib.Path,
) -> dict[str, Any]:
    """Create a fresh station for target and fill its database through the driver.

    Returns what happened: the seconds it took, whether the database is complete, the
    exit code of weewxd and the errors in its log. The log is in the station
    directory, as weewxd.log.
    """
    weewx_bench.installs.create_station(target, weewx_bench.dataset.SQLITE_DATABASE, None)
    database = target.station / 'archive' / weewx_bench.dataset.SQLITE_DATABASE.database_name
    start_ts, stop_ts = data_set.start_ts, weewx_bench.dataset.STOP_TS
    environment = dict(
        os.environ, TZ='UTC', LANG='C.UTF-8', LC_ALL='C.UTF-8', WEEWX_BENCH_NOSYNC='1'
    )
    for name in ('WEEWX_BENCH_TRACE', 'WEEWX_BENCH_FREEZE', 'WEEWX_BENCH_CLOCK'):
        environment.pop(name, None)

    console = None
    gateway = None
    if data_set.profile == 'davis':
        console = weewx_bench.vantage.Console(
            list(weewx_bench.vantage.archive_records(start_ts, stop_ts))
        )
        console.start()
        port = console.port
    else:
        subprocess.run(
            [
                str(target.weectl),
                'extension',
                'install',
                str(weewx_bench.skins.unpack(ECOWITTCUSTOM_URL, work_dir)),
                '--config=%s' % target.config_path,
                '--yes',
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        port = _free_port()
        environment['WEEWX_BENCH_CLOCK'] = str(start_ts)
    _configure(target, data_set.profile, port)

    log_path = target.station / 'weewxd.log'
    start = time.monotonic()
    with open(log_path, 'wb') as log_file:
        process = subprocess.Popen(
            [str(target.venv / 'bin' / 'weewxd'), '--config=%s' % target.config_path],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=environment,
            # On SIGTERM, weewxd sends SIGTERM to its whole process group, which
            # would include weewx-bench.
            start_new_session=True,
        )
        try:
            if data_set.profile == 'ecowitt':
                _wait_for_port(port, process)
                # One upload past the end: the archive period of the last one closes
                # only when a later upload arrives.
                forms = weewx_bench.ecowitt.uploads(
                    start_ts, stop_ts + weewx_bench.ecowitt.INTERVAL
                )
                gateway = weewx_bench.ecowitt.Gateway(
                    'http://127.0.0.1:%d/bench/report' % port,
                    forms,
                    lambda: _last_record(database),
                    AHEAD,
                )
                gateway.start()
            while time.monotonic() - start < TIMEOUT and process.poll() is None:
                if _last_record(database) == stop_ts:
                    break
                if gateway is not None and gateway.error is not None:
                    break
                time.sleep(0.5)
        finally:
            seconds = time.monotonic() - start
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if console is not None:
                console.stop()

    errors = weewx_bench.measure.parse_errors(log_path.read_text(errors='replace'))
    if gateway is not None and gateway.error is not None:
        errors.append('Gateway: %s' % gateway.error)
    return {
        'seconds': seconds,
        'complete': _last_record(database) == stop_ts,
        'exit_code': process.returncode,
        'errors': errors,
    }

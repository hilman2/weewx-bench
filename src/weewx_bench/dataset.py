#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""The benchmark data set, and the databases that hold it."""

from __future__ import annotations

import dataclasses
import importlib.resources
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

import weewx_bench.installs

# The last record of the data set, 30-Jun-2026 12:00 UTC. Reports are generated for
# this time. It is fixed so that every run, on every machine, covers the same days,
# weeks, months and years.
STOP_TS = 1782820800
INTERVAL = 300
# Part of the name of every data set. Increase it whenever generate.py would write
# different values, so that a data set from an older weewx-bench is not reused.
DATA_SET_VERSION = 4
# The stations a data set can imitate. generate.py defines them.
PROFILES = ('davis', 'ecowitt')
DAY = 86400

# These match compose.yaml.
MYSQL_HOST = '127.0.0.1'
MYSQL_PORT = 33060
MYSQL_PROXY_PORT = 33061
MYSQL_USER = 'root'
MYSQL_PASSWORD = 'weewx-bench'
TOXIPROXY_URL = 'http://127.0.0.1:8474'


@dataclasses.dataclass(frozen=True)
class DataSet:
    """Archive records every INTERVAL seconds, for the given number of days up to STOP_TS.

    profile names the station the records imitate, one of PROFILES. With
    left_out, the data set ends that many days before STOP_TS. check gives a driver
    those last days to read.
    """

    days: int
    profile: str = 'davis'
    left_out: int = 0

    @property
    def start_ts(self) -> int:
        return STOP_TS - self.days * DAY

    @property
    def stop_ts(self) -> int:
        return STOP_TS - self.left_out * DAY

    @property
    def name(self) -> str:
        name = 'weewx_bench_v%d_%s_%dd' % (DATA_SET_VERSION, self.profile, self.days)
        if self.left_out:
            name += '_without_last_%dd' % self.left_out
        return name

    def describe(self) -> dict[str, int | str]:
        return {
            'profile': self.profile,
            'days': self.days,
            'interval': INTERVAL,
            'start_ts': self.start_ts,
            'stop_ts': self.stop_ts,
        }


def _generate(
    generator: weewx_bench.installs.Install, database_dict: dict[str, str | int], data_set: DataSet
) -> None:
    script = importlib.resources.files('weewx_bench').joinpath('inside', 'generate.py')
    with importlib.resources.as_file(script) as script_path:
        subprocess.run(
            [
                str(generator.python),
                '-I',
                str(script_path),
                json.dumps(database_dict),
                data_set.profile,
                str(data_set.start_ts),
                str(data_set.stop_ts),
                str(INTERVAL),
            ],
            check=True,
            # The daily summaries start at midnight in the time zone of the process.
            # The reports run with TZ=UTC, so the summaries must be built with it too.
            env=dict(os.environ, TZ='UTC'),
        )


def provide_sqlite(
    generator: weewx_bench.installs.Install, work_dir: pathlib.Path, data_set: DataSet
) -> pathlib.Path:
    """Return the SQLite file with the data set. Generate it if it does not exist yet.

    The file is written under another name and renamed when it is complete. If the
    name exists, the file is complete.
    """
    path = work_dir / 'datasets' / (data_set.name + '.sdb')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + '.partial')
        partial.unlink(missing_ok=True)
        print(
            'Generating %d days of %s archive records into %s'
            % (data_set.days, data_set.profile, path)
        )
        _generate(generator, {'driver': 'weedb.sqlite', 'database_name': str(partial)}, data_set)
        partial.rename(path)
    return path


def provide_mysql(generator: weewx_bench.installs.Install, data_set: DataSet) -> None:
    """Make sure the MariaDB server of compose.yaml holds the data set.

    The generator connects directly, not through the proxy, so that the added
    latency does not slow down the generation.
    """
    _generate(
        generator,
        {
            'driver': 'weedb.mysql',
            'database_name': data_set.name,
            'host': MYSQL_HOST,
            'port': MYSQL_PORT,
            'user': MYSQL_USER,
            'password': MYSQL_PASSWORD,
        },
        data_set,
    )


def mysql_database(data_set: DataSet) -> weewx_bench.installs.Database:
    """Return where the stations find the data set in MariaDB: through the proxy."""
    return weewx_bench.installs.Database(
        binding_entry='archive_mysql',
        database_name=data_set.name,
        database_type={
            'host': MYSQL_HOST,
            'port': str(MYSQL_PROXY_PORT),
            'user': MYSQL_USER,
            'password': MYSQL_PASSWORD,
        },
    )


SQLITE_DATABASE = weewx_bench.installs.Database(
    binding_entry='archive_sqlite', database_name='weewx.sdb', database_type={}
)


def _toxiproxy(method: str, path: str, body: dict[str, object] | None = None) -> None:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        TOXIPROXY_URL + path, data=data, method=method, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def set_latency(latency_ms: int) -> None:
    """Route MariaDB through toxiproxy, with latency_ms added to every response.

    The proxy is created on first use. Toxiproxy delays the data from the server to
    the client, so every query takes at least latency_ms longer. That is what a
    database on another machine costs, and what fewer queries save.
    """
    deadline = time.monotonic() + 30
    while True:
        try:
            _toxiproxy(
                'POST',
                '/proxies',
                {
                    'name': 'mariadb',
                    'listen': '0.0.0.0:3307',
                    'upstream': 'mariadb:3306',
                    'enabled': True,
                },
            )
            break
        except urllib.error.HTTPError as error:
            if error.code == 409:
                # The proxy exists from an earlier run.
                break
            raise
        except urllib.error.URLError:
            if time.monotonic() > deadline:
                raise SystemExit(
                    'Toxiproxy does not answer at %s. Start it with '
                    "'docker compose up -d --wait'." % TOXIPROXY_URL
                ) from None
            time.sleep(1)

    try:
        _toxiproxy('DELETE', '/proxies/mariadb/toxics/latency')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    if latency_ms > 0:
        _toxiproxy(
            'POST',
            '/proxies/mariadb/toxics',
            {
                'name': 'latency',
                'type': 'latency',
                'stream': 'downstream',
                'attributes': {'latency': latency_ms, 'jitter': 0},
            },
        )

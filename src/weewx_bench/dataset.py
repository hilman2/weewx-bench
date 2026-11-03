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
    """Archive records every INTERVAL seconds, for the given number of days up to STOP_TS."""

    days: int

    @property
    def start_ts(self) -> int:
        return STOP_TS - self.days * DAY

    @property
    def name(self) -> str:
        return 'weewx_bench_%dd' % self.days

    def describe(self) -> dict[str, int]:
        return {
            'days': self.days,
            'interval': INTERVAL,
            'start_ts': self.start_ts,
            'stop_ts': STOP_TS,
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
                str(data_set.start_ts),
                str(STOP_TS),
                str(INTERVAL),
            ],
            check=True,
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
        print('Generating %d days of archive records into %s' % (data_set.days, path))
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

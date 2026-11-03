#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Smoke tests. They need neither WeeWX nor a network and run in under a second."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import weewx_bench.installs
import weewx_bench.measure
import weewx_bench.output

INSIDE = pathlib.Path(weewx_bench.installs.__file__).parent / 'inside'


def test_parse_version() -> None:
    assert weewx_bench.installs.parse_version('5.5.2').source == 'weewx==5.5.2'
    assert weewx_bench.installs.parse_version('5.6.0b1').source.endswith('weewx-5.6.0b1.tgz')
    version = weewx_bench.installs.parse_version('master=git+https://github.com/weewx/weewx')
    assert version == weewx_bench.installs.Version('master', 'git+https://github.com/weewx/weewx')


def test_parse_generators() -> None:
    output = (
        '2026-11-03 12:00:00 weectl[1]: INFO weewx.cheetahgenerator: Generated 14 '
        'files for report SeasonsReport in 0.42 seconds\n'
        '2026-11-03 12:00:01 weectl[1]: INFO weewx.imagegenerator: Generated 68 '
        'images for report SeasonsReport in 1.50 seconds\n'
    )
    assert weewx_bench.measure.parse_generators(output) == {
        'SeasonsReport/files': {'count': 14, 'seconds': 0.42},
        'SeasonsReport/images': {'count': 68, 'seconds': 1.5},
    }


def test_trace_hook_counts_sqlite_statements(tmp_path: pathlib.Path) -> None:
    trace_path = tmp_path / 'trace.json'
    script = (
        "import sqlite3; connection = sqlite3.connect(':memory:'); "
        "connection.execute('CREATE TABLE t (x)'); "
        "connection.execute('SELECT * FROM t'); connection.execute('select 1')"
    )
    subprocess.run(
        [sys.executable, '-c', 'import weewx_bench_trace; ' + script],
        check=True,
        cwd=INSIDE,
        env={'WEEWX_BENCH_TRACE': str(trace_path)},
    )
    counts = json.loads(trace_path.read_text())
    assert counts['SELECT'] == 2
    assert counts['CREATE'] == 1


def test_format_table() -> None:
    def version(name: str, wall: float, selects: int) -> dict[str, object]:
        return {
            'label': name,
            'weewx_version': name,
            'statements': {'SELECT': selects, 'BEGIN': 1},
            'differing_files': [],
            'summary': {
                'wall_median': wall,
                'wall_min': wall,
                'wall_max': wall,
                'generators_median': wall,
                'cpu_median': wall,
                'max_rss_kib_median': 46080,
            },
        }

    result = {
        'weewx_bench': '0.1.0',
        'machine': {'system': 'Linux', 'cpu': 'CPU', 'threads': 4},
        'parameters': {
            'backend': 'sqlite',
            'latency_ms': 0,
            'report': 'SeasonsReport',
            'runs': 5,
            'warmup': 1,
            'python': '3.12.0',
            'skins': '5.5.2',
            'data_set': {'days': 730, 'interval': 300},
        },
        'versions': [version('5.5.2', 2.0, 100), version('5.6.0b1', 1.5, 25)],
    }
    table = weewx_bench.output.format_table(result)
    assert '-25.0 %' in table
    assert 'identical to 5.5.2' in table

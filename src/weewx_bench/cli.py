#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Command line of weewx-bench."""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import platform
import sys
from typing import Any

import weewx_bench
import weewx_bench.dataset
import weewx_bench.installs
import weewx_bench.measure
import weewx_bench.output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='weewx-bench',
        description='Compare the report generation time of WeeWX versions on the same data.',
    )
    commands = parser.add_subparsers(dest='command', required=True)

    run = commands.add_parser(
        'run',
        help='Install the versions, generate the data set and measure.',
        description='Install each version into its own venv, generate the data set, and '
        'measure `weectl report run` for every version.',
    )
    run.add_argument(
        'versions',
        nargs='+',
        metavar='VERSION',
        help='5.5.2 for a release, 5.6.0b1 for a pre-release from weewx.com, '
        'or LABEL=SOURCE for anything `uv pip install` accepts. '
        'The first version is the baseline.',
    )
    run.add_argument(
        '--backend',
        choices=['sqlite', 'mysql'],
        default='sqlite',
        help='Database for the data set. mysql needs the containers from '
        'compose.yaml. Default is sqlite.',
    )
    run.add_argument(
        '--latency-ms',
        type=int,
        default=0,
        help='Latency, in milliseconds, added to every MariaDB response. '
        'Only with --backend mysql. Default is 0.',
    )
    run.add_argument(
        '--report',
        default='SeasonsReport',
        help='Report to run, as named in [StdReport]. Default is SeasonsReport.',
    )
    run.add_argument('--runs', type=int, default=5, help='Measured runs per version. Default is 5.')
    run.add_argument(
        '--warmup',
        type=int,
        default=1,
        help='Unmeasured runs per version before the measured ones. Default is 1.',
    )
    run.add_argument(
        '--days',
        type=int,
        default=730,
        help='Days of archive records in the data set. Default is 730.',
    )
    run.add_argument(
        '--python',
        default='3.12',
        help='Python version for the venvs, at least 3.11. Default is 3.12.',
    )
    run.add_argument(
        '--skins',
        choices=['baseline', 'own'],
        default='baseline',
        help='baseline: every version renders the skins of the first version, '
        'as after an upgrade that keeps the skins. own: every version '
        'uses the skins it ships. Default is baseline.',
    )
    run.add_argument(
        '--work-dir',
        type=pathlib.Path,
        default=pathlib.Path('.bench'),
        help='Where venvs, data sets and stations go. Default is .bench.',
    )
    run.add_argument(
        '--results-dir',
        type=pathlib.Path,
        default=pathlib.Path('results'),
        help='Where the result file goes. Default is results.',
    )

    show = commands.add_parser('show', help='Print a result file as a table.')
    show.add_argument('result', type=pathlib.Path, help='A JSON file written by run.')
    return parser


def run(arguments: argparse.Namespace) -> None:
    try:
        versions = [weewx_bench.installs.parse_version(spec) for spec in arguments.versions]
    except ValueError as error:
        sys.exit(str(error))
    labels = [version.label for version in versions]
    if len(set(labels)) != len(labels):
        sys.exit('Every version needs its own label: %s' % ', '.join(labels))
    if arguments.latency_ms and arguments.backend != 'mysql':
        sys.exit('--latency-ms needs --backend mysql.')
    if arguments.runs < 1:
        sys.exit('--runs must be at least 1.')

    work_dir = arguments.work_dir.resolve()
    targets = [
        weewx_bench.installs.install(version, work_dir, arguments.python) for version in versions
    ]
    # The first version generates the data set. All versions then read the same data.
    generator = targets[0]
    data_set = weewx_bench.dataset.DataSet(arguments.days)

    sqlite_file = None
    if arguments.backend == 'sqlite':
        sqlite_file = weewx_bench.dataset.provide_sqlite(generator, work_dir, data_set)
        database = weewx_bench.dataset.SQLITE_DATABASE
    else:
        weewx_bench.dataset.provide_mysql(generator, data_set)
        weewx_bench.dataset.set_latency(arguments.latency_ms)
        database = weewx_bench.dataset.mysql_database(data_set)

    skins_from = generator if arguments.skins == 'baseline' else None
    for target in targets:
        weewx_bench.installs.create_station(target, database, sqlite_file, skins_from)

    measured = weewx_bench.measure.benchmark(
        targets, arguments.report, arguments.runs, arguments.warmup
    )
    result: dict[str, Any] = {
        'weewx_bench': weewx_bench.__version__,
        'created': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'machine': weewx_bench.output.machine(),
        'parameters': {
            'backend': arguments.backend,
            'latency_ms': arguments.latency_ms,
            'report': arguments.report,
            'runs': arguments.runs,
            'warmup': arguments.warmup,
            'python': weewx_bench.installs.run_text(
                [str(generator.python), '-c', 'import platform; print(platform.python_version())']
            ),
            'skins': generator.version.label if skins_from else 'own',
            'data_set': data_set.describe(),
            'data_set_generated_by': generator.version.label,
            'bench_python': platform.python_version(),
        },
        'versions': [],
    }
    for target in targets:
        entry = measured[target.version.label]
        result['versions'].append(
            {
                'label': target.version.label,
                'source': target.version.source,
                'weewx_version': target.weewx_version(),
                'packages': target.freeze(),
                'statements': entry['statements'],
                'differing_files': weewx_bench.measure.differing_files(generator, target),
                'summary': weewx_bench.measure.summarize(entry['runs']),
                'runs': entry['runs'],
            }
        )

    arguments.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    result_path = arguments.results_dir / ('%s-%s.json' % (stamp, arguments.backend))
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    print()
    print(weewx_bench.output.format_table(result))
    print()
    print('Result written to %s' % result_path)


def show(arguments: argparse.Namespace) -> None:
    result = json.loads(arguments.result.read_text())
    print(weewx_bench.output.format_table(result))


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    # The subprocesses write to the same terminal. Without line buffering, their
    # lines would overtake the ones printed here.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    if arguments.command == 'run':
        run(arguments)
    else:
        show(arguments)

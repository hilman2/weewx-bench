#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Command line of weewx-bench."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import pathlib
import platform
import sys
from typing import Any

import weewx_bench
import weewx_bench.compare
import weewx_bench.dataset
import weewx_bench.ingest
import weewx_bench.installs
import weewx_bench.measure
import weewx_bench.output
import weewx_bench.skins


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'versions',
        nargs='+',
        metavar='VERSION',
        help='5.5.2 for a release, 5.6.0b1 for a pre-release from weewx.com, '
        'or LABEL=SOURCE for anything `uv pip install` accepts. '
        'The first version is the baseline.',
    )
    parser.add_argument(
        '--backend',
        choices=['sqlite', 'mysql'],
        default='sqlite',
        help='Database for the data set. mysql needs the containers from '
        'compose.yaml. Default is sqlite.',
    )
    parser.add_argument(
        '--days',
        type=int,
        default=730,
        help='Days of archive records in the data set. Default is 730.',
    )
    parser.add_argument(
        '--python',
        default='3.12',
        help='Python version for the venvs, at least 3.11. Default is 3.12.',
    )
    parser.add_argument(
        '--skins',
        choices=['baseline', 'own'],
        default='baseline',
        help='baseline: every version renders the skins of the first version, '
        'as after an upgrade that keeps the skins. own: every version '
        'uses the skins it ships. Default is baseline.',
    )
    parser.add_argument(
        '--work-dir',
        type=pathlib.Path,
        default=pathlib.Path('.bench'),
        help='Where venvs, data sets and stations go. Default is .bench.',
    )
    parser.add_argument(
        '--results-dir',
        type=pathlib.Path,
        default=pathlib.Path('results'),
        help='Where the results go. Default is results.',
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='weewx-bench',
        description='Compare WeeWX versions on the same data: how long they take to '
        'generate the reports, and whether the reports are the same.',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    reports = ', '.join(weewx_bench.skins.REPORTS)
    profiles = ', '.join(weewx_bench.dataset.PROFILES)

    run = commands.add_parser(
        'run',
        help='Measure how long the versions take to generate reports.',
        description='Install each version into its own venv, generate the data set, and '
        'measure `weectl report run` for every version.',
    )
    _add_common_arguments(run)
    run.add_argument(
        '--latency-ms',
        type=int,
        default=0,
        help='Latency, in milliseconds, added to every MariaDB response. '
        'Only with --backend mysql. Default is 0.',
    )
    run.add_argument(
        '--report',
        action='append',
        help='Report to run, one of %s. Repeat the option for several. '
        'Default is all of them.' % reports,
    )
    run.add_argument(
        '--profile',
        choices=weewx_bench.dataset.PROFILES,
        default='davis',
        help='The station the data set imitates. Default is davis.',
    )
    run.add_argument('--runs', type=int, default=5, help='Measured runs per version. Default is 5.')
    run.add_argument(
        '--warmup',
        type=int,
        default=1,
        help='Unmeasured runs per version before the measured ones. Default is 1.',
    )

    check = commands.add_parser(
        'check',
        help='Compare the reports the versions generate.',
        description='Generate the reports with every version, with the clock frozen, and '
        'compare them with those of the first version.',
    )
    _add_common_arguments(check)
    check.add_argument(
        '--report',
        action='append',
        help='Report to compare, one of %s. Repeat the option for several. '
        'Default is all of them.' % reports,
    )
    check.add_argument(
        '--profile',
        action='append',
        choices=weewx_bench.dataset.PROFILES,
        help='The station the data set imitates, one of %s. Repeat the option for '
        'several. Default is all of them.' % profiles,
    )

    show = commands.add_parser('show', help='Print a result file as a table.')
    show.add_argument('result', type=pathlib.Path, help='A JSON file written by run or check.')
    return parser


def _install(arguments: argparse.Namespace) -> list[weewx_bench.installs.Install]:
    """Check the arguments common to run and check, and install the versions."""
    try:
        versions = [weewx_bench.installs.parse_version(spec) for spec in arguments.versions]
    except ValueError as error:
        sys.exit(str(error))
    labels = [version.label for version in versions]
    if len(set(labels)) != len(labels):
        sys.exit('Every version needs its own label: %s' % ', '.join(labels))
    for report in arguments.report or []:
        if report not in weewx_bench.skins.REPORTS:
            sys.exit(
                'Unknown report %s. Choose from %s.'
                % (report, ', '.join(weewx_bench.skins.REPORTS))
            )
    work_dir = arguments.work_dir.resolve()
    return [
        weewx_bench.installs.install(version, work_dir, arguments.python) for version in versions
    ]


def _prepare_stations(
    arguments: argparse.Namespace,
    targets: list[weewx_bench.installs.Install],
    data_set: weewx_bench.dataset.DataSet,
    latency_ms: int = 0,
) -> None:
    """Provide the data set and give every installation a fresh station with all skins.

    The first version generates the data set. All versions then read the same data.
    """
    work_dir = arguments.work_dir.resolve()
    generator = targets[0]
    sqlite_file = None
    if arguments.backend == 'sqlite':
        sqlite_file = weewx_bench.dataset.provide_sqlite(generator, work_dir, data_set)
        database = weewx_bench.dataset.SQLITE_DATABASE
    else:
        weewx_bench.dataset.provide_mysql(generator, data_set)
        weewx_bench.dataset.set_latency(latency_ms)
        database = weewx_bench.dataset.mysql_database(data_set)

    for target in targets:
        print('Creating the station for %s' % target.version.label)
        weewx_bench.installs.create_station(target, database, sqlite_file)
        weewx_bench.skins.install_skins(target, work_dir)
    if arguments.skins == 'baseline':
        for target in targets[1:]:
            weewx_bench.installs.copy_skins(generator, target)


def _header(
    arguments: argparse.Namespace, targets: list[weewx_bench.installs.Install]
) -> dict[str, Any]:
    """Return the part of a result that run and check share."""
    generator = targets[0]
    return {
        'weewx_bench': weewx_bench.__version__,
        'created': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'machine': weewx_bench.output.machine(),
        'parameters': {
            'backend': arguments.backend,
            'days': arguments.days,
            'python': weewx_bench.installs.run_text(
                [str(generator.python), '-c', 'import platform; print(platform.python_version())']
            ),
            'skins': generator.version.label if arguments.skins == 'baseline' else 'own',
            'data_set_generated_by': generator.version.label,
            'bench_python': platform.python_version(),
        },
    }


def run(arguments: argparse.Namespace) -> None:
    if arguments.latency_ms and arguments.backend != 'mysql':
        sys.exit('--latency-ms needs --backend mysql.')
    if arguments.runs < 1:
        sys.exit('--runs must be at least 1.')
    targets = _install(arguments)
    reports = arguments.report or list(weewx_bench.skins.REPORTS)
    data_set = weewx_bench.dataset.DataSet(arguments.days, arguments.profile)
    _prepare_stations(arguments, targets, data_set, arguments.latency_ms)
    baseline = targets[0]
    version_numbers = {target.version.label: target.weewx_version() for target in targets}

    result = _header(arguments, targets)
    result['parameters'].update(
        latency_ms=arguments.latency_ms,
        reports=reports,
        runs=arguments.runs,
        warmup=arguments.warmup,
        data_set=data_set.describe(),
    )
    result['versions'] = [
        {
            'label': target.version.label,
            'source': target.version.source,
            'weewx_version': version_numbers[target.version.label],
            'packages': target.freeze(),
        }
        for target in targets
    ]
    result['reports'] = []
    # One report at a time, so that every report gets its own time.
    for report in reports:
        print('Measuring %s' % report)
        snapshots = arguments.work_dir.resolve() / 'snapshots' / 'run' / report
        measured = weewx_bench.measure.benchmark(
            targets, [report], arguments.runs, arguments.warmup, snapshots
        )
        entries = []
        for target in targets:
            label = target.version.label
            entry = measured[label]
            entries.append(
                {
                    'label': label,
                    'weewx_version': version_numbers[label],
                    'reference': entry['reference'],
                    'differing_files': weewx_bench.compare.differing_files(
                        snapshots / baseline.version.label,
                        snapshots / label,
                        [version_numbers[baseline.version.label], version_numbers[label]],
                    ),
                    'summary': weewx_bench.measure.summarize(entry['runs']),
                    'runs': entry['runs'],
                }
            )
        result['reports'].append({'report': report, 'versions': entries})

    arguments.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    result_path = arguments.results_dir / ('%s-%s.json' % (stamp, arguments.backend))
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    print()
    print(weewx_bench.output.format_table(result))
    print()
    print('Result written to %s' % result_path)


def check(arguments: argparse.Namespace) -> None:
    if arguments.backend != 'sqlite':
        sys.exit('check works with --backend sqlite only.')
    targets = _install(arguments)
    work_dir = arguments.work_dir.resolve()
    reports = arguments.report or list(weewx_bench.skins.REPORTS)
    profiles = arguments.profile or list(weewx_bench.dataset.PROFILES)
    baseline = targets[0]
    baseline_label = baseline.version.label
    version_numbers = {target.version.label: target.weewx_version() for target in targets}

    def stations_for(profile: str) -> list[weewx_bench.installs.Install]:
        """Return a station per version for one profile, and the baseline again.

        Whatever differs between the two stations of the baseline cannot be compared
        between versions: it is left out, and listed.
        """
        directory = work_dir / 'stations' / profile
        return [
            weewx_bench.installs.Install(t.version, t.venv, directory / t.version.label)
            for t in targets
        ] + [weewx_bench.installs.Install(baseline.version, baseline.venv, directory / '.again')]

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    result_dir = arguments.results_dir / ('%s-check' % stamp)
    result = _header(arguments, targets)
    result['parameters'].update(reports=reports)
    result['versions'] = [
        {
            'label': target.version.label,
            'source': target.version.source,
            'weewx_version': version_numbers[target.version.label],
            'packages': target.freeze(),
        }
        for target in targets
    ]
    result['profiles'] = {}

    # Every station of every profile reads its data at the same time. Each weewxd
    # is a process of its own, and the slowest of them takes a quarter of an hour.
    plan = [(profile, station) for profile in profiles for station in stations_for(profile)]
    print('Reading %d days through the drivers: %d stations at once' % (arguments.days, len(plan)))
    with concurrent.futures.ThreadPoolExecutor(len(plan)) as pool:
        read_in = list(
            pool.map(
                lambda step: weewx_bench.ingest.ingest(
                    step[1], weewx_bench.dataset.DataSet(arguments.days, step[0]), work_dir
                ),
                plan,
            )
        )
    ingests = {
        str(station.station): outcome for (_, station), outcome in zip(plan, read_in, strict=True)
    }

    for profile in profiles:
        data_set = weewx_bench.dataset.DataSet(arguments.days, profile)
        stations = stations_for(profile)
        targets_here, again = stations[:-1], stations[-1]
        baseline_here = stations[0]
        ingested = [ingests[str(station.station)] for station in stations]
        for station in stations:
            weewx_bench.skins.install_skins(station, work_dir)
        if arguments.skins == 'baseline':
            for station in stations[1:]:
                weewx_bench.installs.copy_skins(baseline_here, station)

        def database(target: weewx_bench.installs.Install) -> pathlib.Path:
            name = weewx_bench.dataset.SQLITE_DATABASE.database_name
            return target.station / 'archive' / name

        unstable_columns = weewx_bench.compare.compare_databases(
            database(baseline_here), database(again)
        )
        snapshots = work_dir / 'snapshots' / 'check' / profile
        print('Generating the reports')
        references = {}
        for station in stations:
            references[str(station.station)] = weewx_bench.measure.reference_run(
                station, reports, snapshots / station.station.name
            )
        unstable_files = weewx_bench.compare.differing_files(
            snapshots / baseline_here.station.name,
            snapshots / again.station.name,
            [version_numbers[baseline_label]],
        )

        entries = []
        baseline_errors = set(ingested[0]['errors']) | set(
            references[str(baseline_here.station)]['errors']
        )
        for target, feed in zip(targets_here, ingested, strict=False):
            label = target.version.label
            reference = references[str(target.station)]
            numbers = [version_numbers[baseline_label], version_numbers[label]]
            database_differences = {
                table: {
                    column: count
                    for column, count in columns.items()
                    if column not in unstable_columns.get(table, {})
                }
                for table, columns in weewx_bench.compare.compare_databases(
                    database(baseline_here), database(target)
                ).items()
            }
            database_differences = {t: c for t, c in database_differences.items() if c}
            differing = [
                name
                for name in weewx_bench.compare.differing_files(
                    snapshots / baseline_here.station.name, snapshots / target.station.name, numbers
                )
                if name not in unstable_files
            ]
            diff_path = None
            if differing or database_differences:
                diff_path = result_dir / 'diffs' / ('%s-%s.diff' % (profile, label))
                diff_path.parent.mkdir(parents=True, exist_ok=True)
                diff_path.write_text(
                    weewx_bench.compare.describe_database_differences(
                        database(baseline_here),
                        database(target),
                        database_differences,
                        (baseline_label, label),
                    )
                    + weewx_bench.compare.describe_differences(
                        snapshots / baseline_here.station.name,
                        snapshots / target.station.name,
                        differing,
                        numbers,
                        (baseline_label, label),
                    )
                )
            errors = feed['errors'] + reference['errors']
            entries.append(
                {
                    'label': label,
                    'weewx_version': version_numbers[label],
                    'ingest': feed,
                    'database_differences': database_differences,
                    'files': len(weewx_bench.compare.names(snapshots / target.station.name)),
                    'differing_files': differing,
                    'diff': str(diff_path) if diff_path else None,
                    'errors': errors,
                    'new_errors': [error for error in errors if error not in baseline_errors],
                    'exit_code': reference['exit_code'],
                    'statements': reference['statements'],
                    'generators': reference['generators'],
                }
            )
        result['profiles'][profile] = {
            'data_set': data_set.describe(),
            'unstable_columns': unstable_columns,
            'unstable_files': unstable_files,
            'versions': entries,
        }

    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / 'check.json').write_text(json.dumps(result, indent=2) + '\n')
    summary = weewx_bench.output.format_check(result)
    (result_dir / 'summary.txt').write_text(summary + '\n')
    print()
    print(summary)
    print()
    print('Results written to %s' % result_dir)


def show(arguments: argparse.Namespace) -> None:
    result = json.loads(arguments.result.read_text())
    if 'profiles' in result:
        print(weewx_bench.output.format_check(result))
    else:
        print(weewx_bench.output.format_table(result))


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    # The subprocesses write to the same terminal. Without line buffering, their
    # lines would overtake the ones printed here.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    if arguments.command == 'run':
        run(arguments)
    elif arguments.command == 'check':
        check(arguments)
    else:
        show(arguments)

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
import weewx_bench.compare
import weewx_bench.dataset
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
        'Default is SeasonsReport.' % reports,
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
        weewx_bench.skins.install_extensions(target, work_dir)
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
    reports = arguments.report or ['SeasonsReport']
    data_set = weewx_bench.dataset.DataSet(arguments.days, arguments.profile)
    _prepare_stations(arguments, targets, data_set, arguments.latency_ms)

    snapshots = arguments.work_dir.resolve() / 'snapshots' / 'run'
    measured = weewx_bench.measure.benchmark(
        targets, reports, arguments.runs, arguments.warmup, snapshots
    )
    result = _header(arguments, targets)
    result['parameters'].update(
        latency_ms=arguments.latency_ms,
        reports=reports,
        runs=arguments.runs,
        warmup=arguments.warmup,
        data_set=data_set.describe(),
    )
    result['versions'] = []
    baseline = targets[0]
    for target in targets:
        entry = measured[target.version.label]
        versions = [baseline.weewx_version(), target.weewx_version()]
        result['versions'].append(
            {
                'label': target.version.label,
                'source': target.version.source,
                'weewx_version': target.weewx_version(),
                'packages': target.freeze(),
                'reference': entry['reference'],
                'differing_files': weewx_bench.compare.differing_files(
                    snapshots / baseline.version.label, snapshots / target.version.label, versions
                ),
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


def check(arguments: argparse.Namespace) -> None:
    targets = _install(arguments)
    reports = arguments.report or list(weewx_bench.skins.REPORTS)
    profiles = arguments.profile or list(weewx_bench.dataset.PROFILES)
    baseline = targets[0]
    baseline_label = baseline.version.label
    version_numbers = {target.version.label: target.weewx_version() for target in targets}

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

    for profile in profiles:
        data_set = weewx_bench.dataset.DataSet(arguments.days, profile)
        _prepare_stations(arguments, targets, data_set)
        snapshots = arguments.work_dir.resolve() / 'snapshots' / 'check' / profile

        # Whatever differs between two runs of the same version cannot be compared
        # between versions. It is left out, and listed.
        print('Reference runs for %s, %s' % (baseline_label, profile))
        references = {
            baseline_label: weewx_bench.measure.reference_run(
                baseline, reports, snapshots / baseline_label
            )
        }
        weewx_bench.measure.reference_run(baseline, reports, snapshots / '.baseline-again')
        unstable = weewx_bench.compare.differing_files(
            snapshots / baseline_label, snapshots / '.baseline-again', [version_numbers[baseline_label]]
        )

        entries = []
        for target in targets:
            label = target.version.label
            if label not in references:
                print('Reference run for %s, %s' % (label, profile))
                references[label] = weewx_bench.measure.reference_run(
                    target, reports, snapshots / label
                )
            numbers = [version_numbers[baseline_label], version_numbers[label]]
            differing = [
                name
                for name in weewx_bench.compare.differing_files(
                    snapshots / baseline_label, snapshots / label, numbers
                )
                if name not in unstable
            ]
            diff_path = None
            if differing:
                diff_path = result_dir / 'diffs' / ('%s-%s.diff' % (profile, label))
                diff_path.parent.mkdir(parents=True, exist_ok=True)
                diff_path.write_text(
                    weewx_bench.compare.describe_differences(
                        snapshots / baseline_label,
                        snapshots / label,
                        differing,
                        numbers,
                        (baseline_label, label),
                    )
                )
            baseline_errors = set(references[baseline_label]['errors'])
            entries.append(
                {
                    'label': label,
                    'weewx_version': version_numbers[label],
                    'files': len(weewx_bench.compare.names(snapshots / label)),
                    'differing_files': differing,
                    'diff': str(diff_path) if diff_path else None,
                    'new_errors': [
                        error
                        for error in references[label]['errors']
                        if error not in baseline_errors
                    ],
                    **references[label],
                }
            )
        result['profiles'][profile] = {
            'data_set': data_set.describe(),
            'unstable_files': unstable,
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

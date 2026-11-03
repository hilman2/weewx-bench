#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Describe the machine, and print a result file as a plain text table."""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Any


def machine() -> dict[str, Any]:
    """Return what is known about the machine: system, CPU model and thread count."""
    cpu = platform.processor()
    try:
        with open('/proc/cpuinfo') as cpuinfo:
            for line in cpuinfo:
                if line.startswith('model name'):
                    cpu = line.split(':', 1)[1].strip()
                    break
    except OSError:
        try:
            cpu = subprocess.run(
                ['sysctl', '-n', 'machdep.cpu.brand_string'],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return {'system': platform.platform(), 'cpu': cpu, 'threads': os.cpu_count()}


def _name(version: dict[str, Any]) -> str:
    """Return the label, and the version number if the label is something else."""
    if version['label'] == version['weewx_version']:
        return str(version['label'])
    return '%s (%s)' % (version['label'], version['weewx_version'])


def _change(value: float, baseline: float) -> str:
    if baseline == 0:
        return ''
    return '%+.1f %%' % (100.0 * (value - baseline) / baseline)


def _columns(rows: list[list[str]]) -> list[str]:
    """Return the rows as lines of left-aligned columns."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths, strict=True)]
        lines.append('  '.join(cells).rstrip())
    return lines


def _backend(parameters: dict[str, Any]) -> str:
    if parameters['backend'] == 'sqlite':
        return 'SQLite'
    if parameters.get('latency_ms'):
        return 'MariaDB, %d ms added latency' % parameters['latency_ms']
    return 'MariaDB'


def _skins(parameters: dict[str, Any]) -> str:
    if parameters['skins'] == 'own':
        return 'Each version uses its own skins.'
    return 'Skins of %s.' % parameters['skins']


def _machine(result: dict[str, Any]) -> str:
    machine = result['machine']
    return '%s, %s (%s threads), Python %s' % (
        machine['system'],
        machine['cpu'],
        machine['threads'],
        result['parameters']['python'],
    )


def format_table(result: dict[str, Any]) -> str:
    """Return the result of run as plain text that fits into a mail or a forum post.

    The first version is the baseline. Changes are relative to it, negative is faster.
    """
    parameters = result['parameters']
    data_set = parameters['data_set']
    lines = [
        'weewx-bench %s: %s, %d days of 5-minute %s records'
        % (
            result['weewx_bench'],
            _backend(parameters),
            data_set['days'],
            data_set['profile'],
        ),
        _machine(result),
        'Median of %d runs after %d warm-up run(s). %s'
        % (parameters['runs'], parameters['warmup'], _skins(parameters)),
        '',
    ]

    rows = [
        [
            'Report',
            'Version',
            'Wall time',
            'Change',
            'Min-max',
            'Generators',
            'Change',
            'CPU',
            'Memory',
            'SELECT',
            'Statements',
        ]
    ]
    notes = []
    for report in result['reports']:
        baseline = report['versions'][0]
        for version in report['versions']:
            summary = version['summary']
            statements = version['reference']['statements']
            rows.append(
                [
                    report['report'] if version is baseline else '',
                    _name(version),
                    '%.2f s' % summary['wall_median'],
                    _change(summary['wall_median'], baseline['summary']['wall_median']),
                    '%.2f-%.2f' % (summary['wall_min'], summary['wall_max']),
                    '%.2f s' % summary['generators_median'],
                    _change(summary['generators_median'], baseline['summary']['generators_median']),
                    '%.2f s' % summary['cpu_median'],
                    '%d MB' % (summary['max_rss_kib_median'] / 1024),
                    str(statements.get('SELECT', 0)),
                    str(sum(statements.values())),
                ]
            )
            differing = version['differing_files']
            if differing:
                shown = ', '.join(differing[:5]) + (', ...' if len(differing) > 5 else '')
                notes.append(
                    '%s, %s: %d files differ from %s: %s'
                    % (report['report'], _name(version), len(differing), _name(baseline), shown)
                )
    lines += _columns(rows)
    lines.append('')
    if notes:
        lines += notes
    else:
        lines.append(
            'Every version wrote the same files as %s, apart from the version number.'
            % _name(result['versions'][0])
        )

    lines += [
        '',
        'Wall time: the whole `weectl report run`, including the start of Python.',
        'Generators: the time the generators log for the report.',
        'Statements: every SQL statement, SELECT included, counted in a separate run.',
        'Use `weewx-bench check` for the differences in detail.',
    ]
    return '\n'.join(lines)


def format_check(result: dict[str, Any]) -> str:
    """Return the result of check as plain text that fits into a mail or a forum post."""
    parameters = result['parameters']
    profiles = result['profiles']
    days = next(iter(profiles.values()))['data_set']['days']
    lines = [
        'weewx-bench %s check: %d days of 5-minute records, the last %d through the drivers, %s'
        % (result['weewx_bench'], days, parameters['driver_days'], _backend(parameters)),
        _machine(result),
        'Reports: %s. Clock frozen at 30-Jun-2026 12:01 UTC. %s'
        % (', '.join(parameters['reports']), _skins(parameters)),
        '',
    ]
    rows = [
        [
            'Station',
            'Version',
            'Read in',
            'DB differ',
            'Files',
            'Differ',
            'New errors',
            'SELECT',
            'Statements',
        ]
    ]
    notes = []
    for profile, entry in profiles.items():
        for version in entry['versions']:
            statements = version['statements']
            baseline = version is entry['versions'][0]
            ingest = version['ingest']
            differences = version['database_differences']
            rows.append(
                [
                    profile,
                    _name(version),
                    ('%.0f s' % ingest['seconds']) + ('' if ingest['complete'] else ' INCOMPLETE'),
                    '' if baseline else str(sum(len(c) for c in differences.values())),
                    str(version['files']),
                    '' if baseline else str(len(version['differing_files'])),
                    '' if baseline else str(len(version['new_errors'])),
                    str(statements.get('SELECT', 0)),
                    str(sum(statements.values())),
                ]
            )
            name = '%s, %s' % (profile, _name(version))
            if baseline and version['errors']:
                notes.append(
                    '%s logged %d errors. Other versions are compared with them.'
                    % (name, len(version['errors']))
                )
            if version['exit_code']:
                notes.append('%s: weectl exited with code %d.' % (name, version['exit_code']))
            columns = [
                '%s.%s (%d)' % (table, column, count)
                for table, counts in differences.items()
                for column, count in counts.items()
            ]
            if columns:
                shown = ', '.join(columns[:6]) + (', ...' if len(columns) > 6 else '')
                notes.append('%s: database differs in %s' % (name, shown))
            for error in version.get('new_errors', [])[:3]:
                notes.append('%s: %s' % (name, error))
            if version.get('diff'):
                notes.append('%s: diff in %s' % (name, version['diff']))
        unstable = [
            '%s.%s' % (table, column)
            for table, counts in entry.get('unstable_columns', {}).items()
            for column in counts
        ] + entry['unstable_files']
        if unstable:
            notes.append(
                '%s: not compared, differ between two runs of the baseline: %s'
                % (profile, ', '.join(unstable))
            )
    lines += _columns(rows)
    if notes:
        lines += [''] + notes
    lines += [
        '',
        'Read in: the time weewxd took to take in the days from the driver.',
        'DB differ: columns of the database with values not the same as those of the',
        'first version. Differ: files of the reports that are not the same, after',
        'replacing the version numbers and uptimes.',
        'New errors: log lines of level ERROR that the first version did not log.',
    ]
    return '\n'.join(lines)

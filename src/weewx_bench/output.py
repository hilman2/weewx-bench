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
        'weewx-bench %s: %s, %s, %d days of %d-minute %s records'
        % (
            result['weewx_bench'],
            ', '.join(parameters['reports']),
            _backend(parameters),
            data_set['days'],
            data_set['interval'] // 60,
            data_set['profile'],
        ),
        _machine(result),
        'Median of %d runs after %d warm-up run(s). %s'
        % (parameters['runs'], parameters['warmup'], _skins(parameters)),
        '',
    ]

    rows = [
        [
            'Version',
            'Wall time',
            'Change',
            'Min-max',
            'Generators',
            'CPU',
            'Memory',
            'SELECT',
            'Statements',
        ]
    ]
    baseline = result['versions'][0]
    for version in result['versions']:
        summary = version['summary']
        statements = version['reference']['statements']
        rows.append(
            [
                _name(version),
                '%.2f s' % summary['wall_median'],
                _change(summary['wall_median'], baseline['summary']['wall_median']),
                '%.2f-%.2f' % (summary['wall_min'], summary['wall_max']),
                '%.2f s' % summary['generators_median'],
                '%.2f s' % summary['cpu_median'],
                '%d MB' % (summary['max_rss_kib_median'] / 1024),
                str(statements.get('SELECT', 0)),
                str(sum(statements.values())),
            ]
        )
    lines += _columns(rows)

    lines.append('')
    for version in result['versions'][1:]:
        differing = version['differing_files']
        if differing:
            shown = ', '.join(differing[:5]) + (', ...' if len(differing) > 5 else '')
            lines.append(
                'Output of %s: %d files differ from %s: %s'
                % (_name(version), len(differing), _name(baseline), shown)
            )
        else:
            lines.append(
                'Output of %s: identical to %s, apart from the version number.'
                % (_name(version), _name(baseline))
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
        'weewx-bench %s check: %s, %s, %d days of 5-minute records'
        % (result['weewx_bench'], ', '.join(parameters['reports']), _backend(parameters), days),
        _machine(result),
        'Clock frozen at 30-Jun-2026 12:01 UTC. %s' % _skins(parameters),
        '',
    ]
    rows = [['Station', 'Version', 'Files', 'Differ', 'New errors', 'SELECT', 'Statements']]
    notes = []
    for profile, entry in profiles.items():
        for version in entry['versions']:
            statements = version['statements']
            baseline = version is entry['versions'][0]
            rows.append(
                [
                    profile,
                    _name(version),
                    str(version['files']),
                    '' if baseline else str(len(version['differing_files'])),
                    '' if baseline else str(len(version['new_errors'])),
                    str(statements.get('SELECT', 0)),
                    str(sum(statements.values())),
                ]
            )
            if baseline and version['errors']:
                notes.append(
                    '%s, %s logged %d errors. Other versions are compared with them.'
                    % (profile, _name(version), len(version['errors']))
                )
            if version['exit_code']:
                notes.append(
                    '%s, %s: weectl exited with code %d.'
                    % (profile, _name(version), version['exit_code'])
                )
            for error in version.get('new_errors', [])[:3]:
                notes.append('%s, %s: %s' % (profile, _name(version), error))
            if version.get('diff'):
                notes.append('%s, %s: diff in %s' % (profile, _name(version), version['diff']))
        if entry['unstable_files']:
            notes.append(
                '%s: not compared, differ between two runs of the baseline: %s'
                % (profile, ', '.join(entry['unstable_files']))
            )
    lines += _columns(rows)
    if notes:
        lines += [''] + notes
    lines += [
        '',
        'Differ: files that are not the same as those of the first version, after',
        'replacing the version numbers and uptimes.',
        'New errors: log lines of level ERROR that the first version did not log.',
    ]
    return '\n'.join(lines)

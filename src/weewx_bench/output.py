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


def _change(value: float, baseline: float) -> str:
    if baseline == 0:
        return ''
    return '%+.1f %%' % (100.0 * (value - baseline) / baseline)


def format_table(result: dict[str, Any]) -> str:
    """Return the result as plain text that fits into a mail or a forum post.

    The first version is the baseline. Changes are relative to it, negative is faster.
    """
    parameters = result['parameters']
    data_set = parameters['data_set']
    backend = 'SQLite' if parameters['backend'] == 'sqlite' else 'MariaDB'
    if parameters['backend'] == 'mysql':
        backend += ', %d ms added latency' % parameters['latency_ms']
    if parameters['skins'] == 'own':
        skins = 'Each version uses its own skins.'
    else:
        skins = 'Skins of %s.' % parameters['skins']
    lines = [
        'weewx-bench %s: %s, %s, %d days of %d-minute records'
        % (
            result['weewx_bench'],
            parameters['report'],
            backend,
            data_set['days'],
            data_set['interval'] // 60,
        ),
        '%s, %s (%s threads), Python %s'
        % (
            result['machine']['system'],
            result['machine']['cpu'],
            result['machine']['threads'],
            parameters['python'],
        ),
        'Median of %d runs after %d warm-up run(s). %s'
        % (parameters['runs'], parameters['warmup'], skins),
        '',
    ]

    header = [
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
    rows = [header]
    baseline = result['versions'][0]
    for version in result['versions']:
        summary = version['summary']
        statements = version['statements']
        rows.append(
            [
                version['weewx_version'],
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
    widths = [max(len(row[column]) for row in rows) for column in range(len(header))]
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths, strict=True)]
        lines.append('  '.join(cells).rstrip())

    lines.append('')
    for version in result['versions'][1:]:
        differing = version['differing_files']
        if differing:
            shown = ', '.join(differing[:5]) + (', ...' if len(differing) > 5 else '')
            lines.append(
                'Output of %s: %d files differ from %s: %s'
                % (version['weewx_version'], len(differing), baseline['weewx_version'], shown)
            )
        else:
            lines.append(
                'Output of %s: identical to %s, apart from the version number.'
                % (version['weewx_version'], baseline['weewx_version'])
            )

    lines += [
        '',
        'Wall time: the whole `weectl report run`, including the start of Python.',
        'Generators: the time the generators log for the report.',
        'Statements: every SQL statement, SELECT included, counted in a separate run.',
    ]
    return '\n'.join(lines)

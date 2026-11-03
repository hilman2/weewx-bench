#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Run `weectl report run` and measure it."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

import weewx_bench.dataset
import weewx_bench.installs

GENERATED_PATTERN = re.compile(
    r'Generated (\d+) (files|images) for report (\S+) in ([\d.]+) seconds'
)


def parse_generators(output: str) -> dict[str, dict[str, float]]:
    """Return the times the generators logged, keyed by report and kind.

    For example ``{'SeasonsReport/images': {'count': 54, 'seconds': 1.23}}``. "files"
    comes from the CheetahGenerator, "images" from the ImageGenerator.
    """
    result: dict[str, dict[str, float]] = {}
    for match in GENERATED_PATTERN.finditer(output):
        count, kind, report, seconds = match.groups()
        result['%s/%s' % (report, kind)] = {'count': int(count), 'seconds': float(seconds)}
    return result


def _environment(trace_path: pathlib.Path | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    # The same time zone and locale everywhere: day boundaries and number formats
    # change what the templates compute.
    environment.update(TZ='UTC', LANG='C.UTF-8', LC_ALL='C.UTF-8', PYTHONHASHSEED='0')
    environment.pop('WEEWX_BENCH_TRACE', None)
    if trace_path is not None:
        environment['WEEWX_BENCH_TRACE'] = str(trace_path)
    return environment


def run_once(
    target: weewx_bench.installs.Install, report: str, trace_path: pathlib.Path | None = None
) -> dict[str, Any]:
    """Generate one report from scratch and return what was measured.

    HTML_ROOT is deleted first. Otherwise the ImageGenerator would skip plots that
    look fresh, and the CheetahGenerator would skip existing summaries of past months
    and years, and the run would measure less work than the one before it.
    """
    shutil.rmtree(target.html_root, ignore_errors=True)
    command = [
        str(target.weectl),
        'report',
        'run',
        '--config=%s' % target.config_path,
        '--epoch=%d' % weewx_bench.dataset.STOP_TS,
        report,
    ]
    start = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_environment(trace_path),
        text=True,
    )
    assert process.stdout is not None
    output = process.stdout.read()
    # wait4() instead of wait(): it also returns the CPU time and peak memory of
    # this one child, not of all children so far.
    _, status, usage = os.wait4(process.pid, 0)
    wall = time.perf_counter() - start
    process.returncode = os.waitstatus_to_exitcode(status)
    process.stdout.close()

    generators = parse_generators(output)
    failed = (
        process.returncode != 0
        or 'Traceback' in output
        or ' ERROR ' in output
        or 'Unknown report' in output
        or not generators
    )
    if failed:
        raise RuntimeError(
            'weectl report run failed for %s (exit code %d):\n%s'
            % (target.version.label, process.returncode, output[-4000:])
        )
    return {
        'wall_seconds': wall,
        'cpu_seconds': usage.ru_utime + usage.ru_stime,
        # ru_maxrss is in KiB on Linux, in bytes on macOS.
        'max_rss_kib': usage.ru_maxrss // 1024 if sys.platform == 'darwin' else usage.ru_maxrss,
        'generators': generators,
    }


def count_statements(target: weewx_bench.installs.Install, report: str) -> dict[str, int]:
    """Run the report once with the trace hook and return the SQL statements by keyword."""
    with tempfile.TemporaryDirectory() as directory:
        trace_path = pathlib.Path(directory) / 'trace.json'
        run_once(target, report, trace_path)
        counts: dict[str, int] = json.loads(trace_path.read_text())
    return counts


def benchmark(
    targets: list[weewx_bench.installs.Install], report: str, runs: int, warmup: int
) -> dict[str, dict[str, Any]]:
    """Measure every target and return the results, keyed by label.

    The targets take turns, and the order reverses every round. A machine that gets
    slower or faster during the benchmark then affects all versions alike, instead of
    the one that happens to run last.
    """
    results: dict[str, dict[str, Any]] = {}
    for target in targets:
        print('Counting SQL statements for %s' % target.version.label)
        results[target.version.label] = {
            'statements': count_statements(target, report),
            'runs': [],
        }
    for _ in range(warmup):
        for target in targets:
            print('Warm-up run for %s' % target.version.label)
            run_once(target, report)
    for round_number in range(runs):
        order = targets if round_number % 2 == 0 else list(reversed(targets))
        for target in order:
            measured = run_once(target, report)
            print(
                'Run %d/%d for %s: %.2f s'
                % (round_number + 1, runs, target.version.label, measured['wall_seconds'])
            )
            results[target.version.label]['runs'].append(measured)
    return results


def summarize(runs: list[dict[str, Any]]) -> dict[str, float]:
    """Return median, minimum, maximum and standard deviation of the measured runs."""
    walls = [run['wall_seconds'] for run in runs]
    reports = [
        sum(generator['seconds'] for generator in run['generators'].values()) for run in runs
    ]
    return {
        'wall_median': statistics.median(walls),
        'wall_min': min(walls),
        'wall_max': max(walls),
        'wall_stdev': statistics.stdev(walls) if len(walls) > 1 else 0.0,
        'generators_median': statistics.median(reports),
        'cpu_median': statistics.median(run['cpu_seconds'] for run in runs),
        'max_rss_kib_median': statistics.median(run['max_rss_kib'] for run in runs),
    }


def differing_files(
    baseline: weewx_bench.installs.Install, target: weewx_bench.installs.Install
) -> list[str]:
    """Return the files in HTML_ROOT that differ between two installations.

    Pages name the WeeWX version they were made with. The version numbers are
    replaced before the files are compared. A file that exists on one side only counts as
    differing. An empty list means both versions produced the same report.
    """
    # The skins name a version too, e.g. "Seasons 5.5.2". With the skins of the
    # baseline, that is the baseline's version on both sides. So both version
    # numbers are replaced in both files.
    versions = [baseline.weewx_version().encode(), target.weewx_version().encode()]

    def read(install: weewx_bench.installs.Install, name: str) -> bytes | None:
        path = install.html_root / name
        if not path.is_file():
            return None
        content = path.read_bytes()
        for version in versions:
            content = content.replace(version, b'VERSION')
        return content

    def names(install: weewx_bench.installs.Install) -> set[str]:
        return {
            str(path.relative_to(install.html_root))
            for path in install.html_root.rglob('*')
            if path.is_file()
        }

    return sorted(
        name
        for name in names(baseline) | names(target)
        if read(baseline, name) != read(target, name)
    )

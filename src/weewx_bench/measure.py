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
# The start of every log line: "2026-06-30 12:01:00 weectl[1234]: ".
LOG_PREFIX = re.compile(r'^\S+ \S+ \S+\[\d+\]: ')

# Reference runs see a clock that stands still one minute after the report time,
# as if WeeWX ran the reports right after the last archive record came in.
FROZEN_TS = weewx_bench.dataset.STOP_TS + 60


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


def parse_errors(output: str) -> list[str]:
    """Return the log lines of level ERROR and CRITICAL, and any traceback.

    The time and the process ID are removed, so that the lines of two runs can be
    compared.
    """
    errors = []
    in_traceback = False
    for line in output.splitlines():
        message = LOG_PREFIX.sub('', line)
        if line.startswith('Traceback'):
            in_traceback = True
        if in_traceback or message.startswith(('ERROR ', 'CRITICAL ')):
            errors.append(message)
        # A traceback that Python prints ends with the exception, the first line
        # that is not indented after the header.
        if in_traceback and not line.startswith(('Traceback', ' ')):
            in_traceback = False
    return errors


def _environment(trace_path: pathlib.Path | None, freeze: bool) -> dict[str, str]:
    environment = dict(os.environ)
    # The same time zone and locale everywhere: day boundaries and number formats
    # change what the templates compute.
    environment.update(TZ='UTC', LANG='C.UTF-8', LC_ALL='C.UTF-8', PYTHONHASHSEED='0')
    for name in (
        'WEEWX_BENCH_TRACE',
        'WEEWX_BENCH_FREEZE',
        'WEEWX_BENCH_CLOCK',
        'WEEWX_BENCH_NOSYNC',
    ):
        environment.pop(name, None)
    if trace_path is not None:
        environment['WEEWX_BENCH_TRACE'] = str(trace_path)
    if freeze:
        environment['WEEWX_BENCH_FREEZE'] = str(FROZEN_TS)
    return environment


def run_reports(
    target: weewx_bench.installs.Install,
    reports: list[str],
    trace_path: pathlib.Path | None = None,
    freeze: bool = False,
) -> dict[str, Any]:
    """Generate the reports from scratch and return what was measured and logged.

    HTML_ROOT is deleted first. Otherwise the ImageGenerator would skip plots that
    look fresh, and the CheetahGenerator would skip existing summaries of past months
    and years, and the run would do less work than the one before it.
    """
    shutil.rmtree(target.html_root, ignore_errors=True)
    command = [
        str(target.weectl),
        'report',
        'run',
        '--config=%s' % target.config_path,
        '--epoch=%d' % weewx_bench.dataset.STOP_TS,
        *reports,
    ]
    start = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_environment(trace_path, freeze),
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

    errors = parse_errors(output)
    if 'Unknown report' in output:
        errors.append(output[output.index('Unknown report') :].splitlines()[0])
    return {
        'exit_code': process.returncode,
        'wall_seconds': wall,
        'cpu_seconds': usage.ru_utime + usage.ru_stime,
        # ru_maxrss is in KiB on Linux, in bytes on macOS.
        'max_rss_kib': usage.ru_maxrss // 1024 if sys.platform == 'darwin' else usage.ru_maxrss,
        'generators': parse_generators(output),
        'errors': errors,
        'output': output,
    }


def timed_run(target: weewx_bench.installs.Install, reports: list[str]) -> dict[str, Any]:
    """Run the reports once for the measurement. Fail on any error.

    A version that fails some of the work would look faster than it is.
    """
    result = run_reports(target, reports)
    if result['exit_code'] != 0 or result['errors'] or not result['generators']:
        raise RuntimeError(
            'weectl report run failed for %s (exit code %d):\n%s'
            % (target.version.label, result['exit_code'], result['output'][-4000:])
        )
    del result['output'], result['errors'], result['exit_code']
    return result


def reference_run(
    target: weewx_bench.installs.Install, reports: list[str], snapshot: pathlib.Path
) -> dict[str, Any]:
    """Run the reports once with the clock frozen and the statements counted.

    The output is copied to snapshot, for the comparison between versions. Errors
    do not stop the run: they are part of what the comparison reports.
    """
    with tempfile.TemporaryDirectory() as directory:
        trace_path = pathlib.Path(directory) / 'trace.json'
        result = run_reports(target, reports, trace_path, freeze=True)
        try:
            statements: dict[str, int] = json.loads(trace_path.read_text())
        except (OSError, ValueError):
            # The process died before it could write the counts.
            statements = {}
    shutil.rmtree(snapshot, ignore_errors=True)
    if target.html_root.exists():
        shutil.copytree(target.html_root, snapshot)
    else:
        snapshot.mkdir(parents=True)
    return {
        'exit_code': result['exit_code'],
        'statements': statements,
        'generators': {key: int(value['count']) for key, value in result['generators'].items()},
        'errors': result['errors'],
    }


def benchmark(
    targets: list[weewx_bench.installs.Install],
    reports: list[str],
    runs: int,
    warmup: int,
    snapshots: pathlib.Path,
) -> dict[str, dict[str, Any]]:
    """Measure every target and return the results, keyed by label.

    Each target first gets a reference run, whose output lands in snapshots/LABEL.
    The timed runs follow. The targets take turns, and the order reverses every
    round. A machine that gets slower or faster during the benchmark then affects
    all versions alike, instead of the one that happens to run last.
    """
    results: dict[str, dict[str, Any]] = {}
    for target in targets:
        print('Reference run for %s' % target.version.label)
        reference = reference_run(target, reports, snapshots / target.version.label)
        if reference['exit_code'] != 0 or reference['errors']:
            raise RuntimeError(
                'weectl report run failed for %s:\n%s'
                % (target.version.label, '\n'.join(reference['errors'][-40:]))
            )
        results[target.version.label] = {'reference': reference, 'runs': []}
    for _ in range(warmup):
        for target in targets:
            print('Warm-up run for %s' % target.version.label)
            timed_run(target, reports)
    for round_number in range(runs):
        order = targets if round_number % 2 == 0 else list(reversed(targets))
        for target in order:
            measured = timed_run(target, reports)
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

#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""Install WeeWX versions into their own venvs and create a station for each."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.resources
import json
import pathlib
import re
import shutil
import subprocess
import urllib.request

import configobj

# A release on PyPI, or a pre-release such as 5.6.0b1, which only weewx.com has.
VERSION_PATTERN = re.compile(r'^\d+\.\d+(\.\d+)?((a|b|rc)\d+)?$')
DEVELOPMENT_URL = 'https://weewx.com/downloads/development_versions/weewx-%s.tgz'


@dataclasses.dataclass(frozen=True)
class Version:
    """A WeeWX version to benchmark.

    label is the name in the results. source is what `uv pip install` gets: a
    requirement, a URL, or a path.
    """

    label: str
    source: str


def parse_version(spec: str) -> Version:
    """Turn a command line argument into a Version.

    Accepted forms:

    - ``5.5.2``: the release from PyPI.
    - ``5.6.0b1``: a pre-release from weewx.com.
    - ``LABEL=SOURCE``: anything `uv pip install` accepts, e.g. a URL to a
      .tgz, a path to a source tree, or ``git+https://github.com/weewx/weewx@master``.
    """
    if '=' in spec:
        label, source = spec.split('=', 1)
        if not label or not source:
            raise ValueError('Expected LABEL=SOURCE, got %r' % spec)
        return Version(label, source)
    match = VERSION_PATTERN.match(spec)
    if not match:
        raise ValueError('%r is not a version number. Use LABEL=SOURCE for anything else.' % spec)
    if int(spec.split('.')[0]) < 5:
        raise ValueError('weewx-bench needs WeeWX 5.0 or later. %s has no weectl.' % spec)
    if match.group(2):
        return Version(spec, DEVELOPMENT_URL % spec)
    return Version(spec, 'weewx==%s' % spec)


@dataclasses.dataclass(frozen=True)
class Install:
    """A WeeWX version, installed into a venv, with its own station directory."""

    version: Version
    venv: pathlib.Path
    station: pathlib.Path

    @property
    def python(self) -> pathlib.Path:
        return self.venv / 'bin' / 'python'

    @property
    def weectl(self) -> pathlib.Path:
        return self.venv / 'bin' / 'weectl'

    @property
    def config_path(self) -> pathlib.Path:
        return self.station / 'weewx.conf'

    @property
    def html_root(self) -> pathlib.Path:
        return self.station / 'public_html'

    def weewx_version(self) -> str:
        return run_text([str(self.python), '-c', 'import weewx; print(weewx.__version__)'])

    def freeze(self) -> list[str]:
        """Return the installed packages, as `uv pip freeze` lists them."""
        output = run_text(['uv', 'pip', 'freeze', '--python', str(self.python)])
        return output.splitlines()


def run_text(command: list[str]) -> str:
    """Run a command and return its standard output, stripped. Fail loudly."""
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _constraints_text() -> str:
    return importlib.resources.files('weewx_bench').joinpath('constraints.txt').read_text()


def _fetch_tgz(source: str, downloads: pathlib.Path) -> str:
    """Return a path uv accepts for a .tgz source.

    uv refuses source archives that end in .tgz, and that is how weewx.com names
    them. The archive is downloaded or copied once, as .tar.gz.
    """
    name = source.rstrip('/').rsplit('/', 1)[-1]
    target = downloads / (name[: -len('.tgz')] + '.tar.gz')
    if not target.exists():
        downloads.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.partial')
        if '://' in source:
            print('Downloading %s' % source)
            urllib.request.urlretrieve(source, partial)
        else:
            shutil.copyfile(source, partial)
        partial.rename(target)
    return str(target)


def install(version: Version, work_dir: pathlib.Path, python: str) -> Install:
    """Create the venv for a version, unless one with the same inputs exists.

    The venv is reused if source, Python version and constraints are unchanged. A
    source that changes under the same name, like a git branch, is therefore not
    picked up again. Delete the venv to force a new installation.
    """
    venv = work_dir / 'venvs' / version.label
    constraints = _constraints_text()
    marker = {
        'source': version.source,
        'python': python,
        'constraints': hashlib.sha256(constraints.encode()).hexdigest(),
    }
    marker_path = venv / 'weewx-bench.json'
    try:
        current = json.loads(marker_path.read_text())
    except (OSError, ValueError):
        current = None

    if current != marker:
        if venv.exists():
            shutil.rmtree(venv)
        print('Installing %s from %s' % (version.label, version.source))
        source = version.source
        if source.endswith('.tgz'):
            source = _fetch_tgz(source, work_dir / 'downloads')
        work_dir.mkdir(parents=True, exist_ok=True)
        constraints_path = work_dir / 'constraints.txt'
        constraints_path.write_text(constraints)
        subprocess.run(['uv', 'venv', '--quiet', '--python', python, str(venv)], check=True)
        subprocess.run(
            [
                'uv',
                'pip',
                'install',
                '--quiet',
                '--compile-bytecode',
                '--python',
                str(venv / 'bin' / 'python'),
                '--constraint',
                str(constraints_path),
                source,
                # The hook in weewx_bench_hook.py freezes the clock with it.
                'time-machine',
            ],
            check=True,
        )
        marker_path.write_text(json.dumps(marker))

    result = Install(version, venv, work_dir / 'stations' / version.label)
    _install_hook(result)
    return result


def _install_hook(target: Install) -> None:
    """Make weewx_bench_hook load at every start of the venv's Python.

    A .pth file in site-packages is the only place that runs code before weectl
    imports anything, without changing weectl or WeeWX.
    """
    site_packages = pathlib.Path(
        run_text(
            [str(target.python), '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])']
        )
    )
    hook = importlib.resources.files('weewx_bench').joinpath('inside', 'weewx_bench_hook.py')
    (site_packages / 'weewx_bench_hook.py').write_text(hook.read_text())
    (site_packages / 'weewx_bench_hook.pth').write_text('import weewx_bench_hook\n')


@dataclasses.dataclass(frozen=True)
class Database:
    """Where the stations find the benchmark data.

    binding_entry is the [[Databases]] entry in weewx.conf that the data binding
    points to. database_name goes into that entry, database_type into the
    [DatabaseTypes] entry the database uses.
    """

    binding_entry: str
    database_name: str
    database_type: dict[str, str]


def create_station(target: Install, database: Database, sqlite_file: pathlib.Path | None) -> None:
    """Create a fresh station directory for one installation.

    The station is made by the installation's own `weectl station create`, so the
    configuration file is the one this version writes for a new user. Only what a
    benchmark needs is changed afterwards: logging to the console and the database.
    """
    if target.station.exists():
        shutil.rmtree(target.station)
    subprocess.run(
        [
            str(target.weectl),
            'station',
            'create',
            str(target.station),
            '--no-prompt',
            '--driver=weewx.drivers.simulator',
            '--units=us',
            '--location=weewx-bench',
            '--latitude=50.0',
            '--longitude=0.0',
            '--altitude=300,meter',
            '--config=%s' % target.config_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )

    config = configobj.ConfigObj(str(target.config_path), encoding='utf-8')
    config['debug'] = '0'
    # weectl logs to syslog by default. The measurement reads the generator times
    # from the log, so it has to reach the console, which is stdout.
    config['Logging'] = {'root': {'level': 'INFO', 'handlers': ['console']}}
    config['DataBindings']['wx_binding']['database'] = database.binding_entry
    entry = config['Databases'][database.binding_entry]
    entry['database_name'] = database.database_name
    type_name = entry['database_type']
    config['DatabaseTypes'][type_name].update(database.database_type)
    config.write()

    if sqlite_file is not None:
        archive = target.station / 'archive'
        archive.mkdir(exist_ok=True)
        shutil.copyfile(sqlite_file, archive / database.database_name)


def copy_skins(source: Install, target: Install) -> None:
    """Replace the skins of target with those of source."""
    shutil.rmtree(target.station / 'skins')
    shutil.copytree(source.station / 'skins', target.station / 'skins')

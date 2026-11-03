#
#    Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
#    See the file LICENSE for your full rights.
#
"""The skins every station gets besides the ones WeeWX ships."""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

import configobj

import weewx_bench.installs


@dataclasses.dataclass(frozen=True)
class Extension:
    """A skin, installed as a WeeWX extension from a fixed release.

    report is the section weewx-bench writes into [StdReport] for it, skin the
    directory the extension puts under SKIN_ROOT. The report writes into
    public_html/html_root. extras go into the [[[Extras]]] of the report.
    """

    report: str
    skin: str
    url: str
    html_root: str
    extras: dict[str, str] = dataclasses.field(default_factory=dict)


# The three most-starred WeeWX skins on GitHub on 03-Nov-2026.
EXTENSIONS = (
    Extension(
        report='Belchertown',
        skin='Belchertown',
        url='https://github.com/poblabs/weewx-belchertown/releases/download/'
        'weewx-belchertown-3.0.0/weewx-belchertown-3.0.0.tar.gz',
        html_root='belchertown',
        # Both fetch data from the internet while the report runs.
        extras={'forecast_enabled': '0', 'forecast_alert_enabled': '0'},
    ),
    Extension(
        report='NeoWXMaterial',
        skin='neowx-material',
        url='https://github.com/neoground/neowx-material/releases/download/1.11/'
        'neowx-material-1.11.zip',
        html_root='neowx',
    ),
    Extension(
        report='WdcReport',
        skin='weewx-wdc',
        url='https://github.com/Daveiano/weewx-wdc/releases/download/v3.5.1/weewx-wdc-v3.5.1.zip',
        html_root='wdc',
    ),
)

# Reports with a single plot: the average temperature of a year, at different
# aggregate_interval values. Tom Keffer named this case in the beta thread of 5.6.0
# as the one where the speed-up shows most. Keyed by report name.
SERIES = {'Series1h': '1h', 'Series3h': '3h', 'SeriesDay': 'day'}

SERIES_SKIN = """# Written by weewx-bench.
[ImageGenerator]
    image_width = 800
    image_height = 300
    [[year_images]]
        time_length = 31536000
        aggregate_type = avg
        aggregate_interval = %s
        [[[yearouttemp]]]
            [[[[outTemp]]]]

[Generators]
    generator_list = weewx.imagegenerator.ImageGenerator
"""

REPORTS = ('SeasonsReport',) + tuple(extension.report for extension in EXTENSIONS) + tuple(SERIES)


def unpack(url: str, work_dir: pathlib.Path) -> pathlib.Path:
    """Download and unpack an extension release once. Return the directory with install.py.

    `weectl extension install` takes an archive only if it has a single top-level
    directory. The WDC archive has several, so every extension is installed from a
    directory instead.
    """
    name = url.rsplit('/', 1)[-1]
    target = work_dir / 'extensions' / name
    if not target.exists():
        archive = work_dir / 'downloads' / name
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            print('Downloading %s' % url)
            partial = archive.with_name(name + '.partial')
            urllib.request.urlretrieve(url, partial)
            partial.rename(archive)
        partial_dir = target.with_name(name + '.partial')
        shutil.rmtree(partial_dir, ignore_errors=True)
        if name.endswith('.zip'):
            with zipfile.ZipFile(archive) as zip_file:
                zip_file.extractall(partial_dir)
        else:
            with tarfile.open(archive) as tar_file:
                # The filter rejects paths outside the target. Python 3.10.11 and
                # earlier do not have it.
                if hasattr(tarfile, 'data_filter'):
                    tar_file.extractall(partial_dir, filter='data')
                else:
                    tar_file.extractall(partial_dir)
        partial_dir.rename(target)
    installers = sorted(target.rglob('install.py'), key=lambda path: len(path.parts))
    return installers[0].parent


def install_skins(target: weewx_bench.installs.Install, work_dir: pathlib.Path) -> None:
    """Install every skin of EXTENSIONS and SERIES into the station of an installation.

    Each installation runs its own `weectl extension install`. Afterwards the
    report sections are written anew. The installers disagree on where the output
    goes: Belchertown writes outside public_html, WDC into it, on top of Seasons,
    and NeoWX-Material changes no report at all.
    """
    for extension in EXTENSIONS:
        subprocess.run(
            [
                str(target.weectl),
                'extension',
                'install',
                str(unpack(extension.url, work_dir)),
                '--config=%s' % target.config_path,
                '--yes',
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    config = configobj.ConfigObj(str(target.config_path), encoding='utf-8')
    for extension in EXTENSIONS:
        config['StdReport'][extension.report] = {
            'skin': extension.skin,
            'HTML_ROOT': 'public_html/%s' % extension.html_root,
            'enable': 'true',
            'Extras': dict(extension.extras),
        }
    for report, interval in SERIES.items():
        skin = 'bench-%s' % report.lower()
        (target.station / 'skins' / skin).mkdir()
        (target.station / 'skins' / skin / 'skin.conf').write_text(SERIES_SKIN % interval)
        config['StdReport'][report] = {
            'skin': skin,
            'HTML_ROOT': 'public_html/%s' % report.lower(),
            'enable': 'true',
        }
    config.write()

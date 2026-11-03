# weewx-bench

Does a new WeeWX version still generate the same reports? And how long does it
take? weewx-bench answers both on your own machine, with results you can
repeat and compare with others.

It installs each WeeWX version you name into its own Python environment, gives
all of them the same database and the same skins, and runs `weectl report run`
for each.

- `weewx-bench check` compares the files the versions write.
- `weewx-bench run` measures how long they take.

## Requirements

- Linux or macOS. Windows does not work.
- [uv](https://docs.astral.sh/uv/). It downloads a suitable Python by itself.
- git
- Access to PyPI, GitHub and weewx.com
- Up to 1 GB of disk space, most of it in `.bench`, the rest for uv's cache
  and Python
- To test with MariaDB: Docker with Compose, and the ports 8474, 33060 and
  33061 on 127.0.0.1 must be free

weewx-bench works with WeeWX 5.0 and later. Earlier versions have no `weectl`.

## Checking the reports

Compare the beta 5.6.0b1 with the release 5.5.2:

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench check 5.5.2 5.6.0b1
```

The first version you name is the baseline. The others are compared with it.

Every version generates four reports, for two stations: one like a Davis
Vantage, one like an Ecowitt gateway. The reports are those of the Seasons
skin and of the three most popular skins on GitHub: Belchertown, NeoWX-Material
and WDC. Then weewx-bench compares every file with the one the baseline wrote:

```
weewx-bench 0.3.0 check: SeasonsReport, Belchertown, NeoWXMaterial, WdcReport, SQLite, 730 days of 5-minute records
Linux-7.0.0-38-generic-x86_64-with-glibc2.43, AMD Ryzen AI 9 HX 470 w/ Radeon 890M (24 threads), Python 3.12.15
Clock frozen at 30-Jun-2026 12:01 UTC. Skins of 5.5.2.

Station  Version  Files  Differ  New errors  SELECT  Statements
davis    5.5.2    448                        249825  249833
davis    5.6.0b1  448    1       0           139546  139554
ecowitt  5.5.2    444                        264635  264643
ecowitt  5.6.0b1  444    1       0           148844  148852

davis, 5.6.0b1: diff in results/20261103-135327-check/diffs/davis-5.6.0b1.diff
ecowitt, 5.6.0b1: diff in results/20261103-135327-check/diffs/ecowitt-5.6.0b1.diff
```

Here, one file differs for each station: the WDC table of June 2024, in the
row of the first day of the data, which starts at noon. 5.5.2 shows the daily
summary of the whole day, which includes the archive record of 12:00. 5.6.0b1
shows the archive records after 12:00.

| Column | |
|---|---|
| Files | Files the version wrote |
| Differ | Files that are not the same as those of the baseline |
| New errors | Log lines of level ERROR that the baseline did not log |
| SELECT, Statements | SQL statements sent to the database |

The differences are written as a diff, one file per station and version, in
`results`. The path is below the table.

Before the files are compared, weewx-bench replaces the WeeWX version numbers
and the uptimes, which differ anyway. Everything else has to match, down to
the last byte of every plot.

A difference is not always a bug. A new version may fix a calculation, and
then the numbers on a page change. The diff shows where, and the database is
under `.bench/datasets` to work out which version is right.

## Measuring the time

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench run 5.5.2 5.6.0b1
```

This runs the Seasons report five times per version, after one run to warm up:

```
weewx-bench 0.3.0: SeasonsReport, SQLite, 730 days of 5-minute davis records
Linux-7.0.0-38-generic-x86_64-with-glibc2.43, AMD Ryzen AI 9 HX 470 w/ Radeon 890M (24 threads), Python 3.12.15
Median of 5 runs after 1 warm-up run(s). Skins of 5.5.2.

Version  Wall time  Change   Min-max    Generators  CPU     Memory  SELECT  Statements
5.5.2    1.15 s     +0.0 %   1.10-1.17  1.04 s      1.14 s  45 MB   26898   26902
5.6.0b1  0.95 s     -17.2 %  0.93-0.98  0.84 s      0.95 s  45 MB   13797   13801

Output of 5.6.0b1: identical to 5.5.2, apart from the version number.
```

| Column | |
|---|---|
| Wall time | Median of the whole `weectl report run`, including the start of Python |
| Change | Wall time relative to the baseline. Negative is faster |
| Min-max | The fastest and the slowest run |
| Generators | The time the generators log for the report |
| CPU | CPU time the process used |
| Memory | Peak memory of the process |
| SELECT, Statements | SQL statements sent to the database, from a separate run |

If the minimum and maximum of two versions overlap, the difference is within
the noise of your machine. Close other programs and use more `--runs`.

Below the table, weewx-bench says whether the versions wrote the same files.
If they did not, the time comparison may not be fair. `check` shows the
differences.

## Your first run

The first run takes a few minutes. It installs the versions and the skins, and
it generates two years of archive records for each station. Later runs reuse
all of it. Everything goes into the directory `.bench`, and the results into
`results`, both in the current directory. To remove everything, delete these
two directories.

To print a result again:

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench show results/20261103-130000-sqlite.json
```

The file `check.json` in the directory of a check works the same way. The
result files hold every run, the installed packages, and the machine. Attach
them if you share your results.

## Which versions you can name

| Argument | Installs |
|---|---|
| `5.5.2` | the release from PyPI |
| `5.6.0b1` | the pre-release from weewx.com |
| `master=git+https://github.com/weewx/weewx@master` | a branch, tag or commit from git |
| `mine=/home/me/weewx` | a source tree on your disk |
| `beta=https://example.com/weewx-5.6.0b1.tgz` | any archive `pip` can install |

Before the `=` is the name the version gets in the table. A version is
installed once. If its source changes under the same name, as a git branch
does, delete its directory under `.bench/venvs` to install it again.

## The stations

Both stations report every five minutes, for two years up to 30 June 2026
12:00 UTC. They see the same weather, through different sensors.

| Station | Sensors | Units in the database |
|---|---|---|
| `davis` | Vantage Pro2 Plus with solar radiation, UV and ET, a soil and leaf station | US |
| `ecowitt` | WS90 outdoor array, two WH31 thermo-hygrometers, WH45 air quality, WH57 lightning, two WH51 soil moisture, WN34 soil temperature | METRICWX |

Every value is computed from its timestamp, so the archive records are the
same on every machine. The daily summaries for wind can differ in the last
digit between Linux and macOS, because WeeWX computes the wind vectors with
the sine and cosine of the operating system.

## The skins

| Skin | Version | Report |
|---|---|---|
| Seasons | the one of the baseline | `SeasonsReport` |
| [Belchertown](https://github.com/poblabs/weewx-belchertown) | 3.0.0 | `Belchertown` |
| [NeoWX-Material](https://github.com/neoground/neowx-material) | 1.11 | `NeoWXMaterial` |
| [WDC](https://github.com/Daveiano/weewx-wdc) | 3.5.1 | `WdcReport` |

Each version installs the skins with its own `weectl extension install`.
Forecasts and weather alerts in Belchertown are turned off, because they load
data from the internet.

By default every version renders the skins of the baseline, Seasons included.
That is what you get after an upgrade that keeps your skins, and it means the
results show the difference between the WeeWX versions alone. With
`--skins own`, every version uses the Seasons skin it ships.

## Testing with MariaDB

A database on another machine makes every query cost a network round trip.
To measure that, start MariaDB with a proxy in front of it. This needs a clone
of this repository:

```
git clone https://github.com/hilman2/weewx-bench
cd weewx-bench
docker compose up -d --wait
uv run weewx-bench run 5.5.2 5.6.0b1 --backend mysql --latency-ms 2
```

`--latency-ms` adds that many milliseconds to every answer from MariaDB.
Without it, MariaDB is as close as it can be.

With latency, a run takes much longer. WeeWX 5.5.2 sends about 26,000
statements for the Seasons report, so 2 ms add about a minute to each run.
The example above takes about 12 minutes. Use fewer `--runs` or `--days` for
a first look.

`check` takes `--backend mysql` as well, without latency. When you are done:

```
docker compose down -v
```

## Options

For both `check` and `run`:

| Option | Default | |
|---|---|---|
| `--backend` | `sqlite` | `sqlite` or `mysql` |
| `--days` | `730` | Days of archive records in the database |
| `--python` | `3.12` | Python version for the installations, at least 3.11 |
| `--skins` | `baseline` | `baseline` or `own`, see [The skins](#the-skins) |
| `--work-dir` | `.bench` | Installations, databases and stations |
| `--results-dir` | `results` | Result files |

For `check`:

| Option | Default | |
|---|---|---|
| `--report` | all four | A report from [The skins](#the-skins). Repeat it for several |
| `--profile` | both | `davis` or `ecowitt`. Repeat it for both |

For `run`:

| Option | Default | |
|---|---|---|
| `--report` | `SeasonsReport` | A report from [The skins](#the-skins). Repeat it for several |
| `--profile` | `davis` | `davis` or `ecowitt` |
| `--runs` | `5` | Measured runs per version |
| `--warmup` | `1` | Runs per version before the measured ones |
| `--latency-ms` | `0` | Milliseconds added to every MariaDB answer |

## How it works

You only need this section to judge the results or to change weewx-bench.

Every version gets its own station, created by its own
`weectl station create`, with the Simulator as driver. All stations of a
profile read the same database, which the first version writes. Reports are
generated for the last archive record, with `TZ=UTC`.

Before each run, weewx-bench deletes `HTML_ROOT`. Otherwise the image
generator would skip plots it considers fresh, and the Cheetah generator would
skip the summaries of past months and years, and each run would do less work
than the one before.

For the comparison, the clock stands still at 30 June 2026 12:01 UTC. Skins
put the current time into their pages, and without this, two runs of the same
version would never write the same files. To be sure, `check` runs the
baseline twice. A file that still differs between these two runs is left out
of the comparison and listed below the table.

The SQL statements are counted in the same run. Counting slows WeeWX down, and
the frozen clock stops the generators from logging their times, so this run is
never one of the timed ones. weewx-bench puts a small module into each
installation that hooks into the database drivers, `sqlite3` and PyMySQL, and
uses [time-machine](https://github.com/adamchainz/time-machine) for the clock.

In `run`, the versions take turns, and the order reverses every round. If the
machine gets faster or slower during the benchmark, this affects all versions
alike.

All installations get the same versions of Pillow, Cheetah and the other
dependencies, from `src/weewx_bench/constraints.txt`.

## License

GPL-3.0-or-later, like WeeWX.

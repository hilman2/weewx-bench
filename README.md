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
- About 2 GB of disk space for `check` with two versions, and 400 MB more for
  each further version. Most of it is in `.bench`.
- To test with MariaDB: Docker with Compose, and the ports 8474, 33060 and
  33061 on 127.0.0.1 must be free

weewx-bench works with WeeWX 5.0 and later. Earlier versions have no `weectl`.

## Checking a new version

Compare the beta 5.6.0b1 with the release 5.5.2:

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench check 5.5.2 5.6.0b1
```

The first version you name is the baseline. The others are compared with it.

For two stations, one like a Davis Vantage and one like an Ecowitt gateway,
every version first takes in two years of readings through a real driver and
`weewxd`. Then it generates seven reports from its own database: those of the
Seasons skin, of the three most popular skins on GitHub, and three plots of a
long series, see [The reports](#the-reports). weewx-bench compares the
databases, then every file of the reports, with those of the baseline:

```
weewx-bench 0.4.0 check: 730 days of 5-minute records through the drivers, SQLite
Linux-7.0.0-38-generic-x86_64-with-glibc2.43, AMD Ryzen AI 9 HX 470 w/ Radeon 890M (24 threads), Python 3.12.15
Reports: SeasonsReport, Belchertown, NeoWXMaterial, WdcReport, Series1h, Series3h, SeriesDay. Clock frozen at 30-Jun-2026 12:01 UTC. Skins of 5.5.2.

Station  Version  Read in  DB differ  Files  Differ  New errors  SELECT  Statements
davis    5.5.2    523 s               447                        261846  261857
davis    5.6.0b1  492 s    0          447    0       0           139474  139485
ecowitt  5.5.2    1269 s              451                        282554  282565
ecowitt  5.6.0b1  1193 s   0          451    0       0           152792  152803
```

Here, 5.6.0b1 stores the same values as 5.5.2, down to the last digit, and
writes the same reports from them. It sends about half as many SQL statements
for the reports.

| Column | |
|---|---|
| Read in | The time `weewxd` took to take in the data. All versions read at the same time, so this is a rough figure |
| DB differ | Columns of the database whose values are not the same as those of the baseline |
| Files | Files the version wrote |
| Differ | Files that are not the same as those of the baseline |
| New errors | Log lines of level ERROR that the baseline did not log |
| SELECT, Statements | SQL statements sent to the database |

The differences are written as a diff, one file per station and version, in
`results`: first the database, with a few rows for each column, then the
files. The path is below the table.

Before the files are compared, weewx-bench replaces the WeeWX version numbers
and the uptimes, which differ anyway. Everything else has to match, down to
the last byte of every plot.

A difference is not always a bug. A new version may fix a calculation, and
then the numbers in the database or on a page change. The diff shows where.
The databases are under `.bench/stations`, one per version, to work out which
version is right.

## Measuring the time

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench run 5.5.2 5.6.0b1
```

This runs every report on its own, five times per version, after one run to
warm up. With MariaDB and 1 ms added latency, `Series3h` takes 4.05 s with
5.5.2 and 0.34 s with 5.6.0b1:

```
weewx-bench 0.4.0: SQLite, 730 days of 5-minute davis records
Linux-7.0.0-38-generic-x86_64-with-glibc2.43, AMD Ryzen AI 9 HX 470 w/ Radeon 890M (24 threads), Python 3.12.15
Median of 5 runs after 1 warm-up run(s). Skins of 5.5.2.

Report         Version  Wall time  Change   Min-max    Generators  Change   CPU     Memory  SELECT  Statements
SeasonsReport  5.5.2    1.11 s     +0.0 %   1.10-1.16  1.02 s      +0.0 %   1.11 s  45 MB   26898   26902
               5.6.0b1  0.94 s     -15.8 %  0.93-1.00  0.82 s      -19.6 %  0.94 s  45 MB   13797   13801
Belchertown    5.5.2    1.11 s     +0.0 %   1.10-1.17  0.76 s      +0.0 %   1.10 s  55 MB   19065   19069
               5.6.0b1  1.09 s     -2.4 %   1.02-1.12  0.74 s      -2.6 %   1.09 s  55 MB   12269   12273
NeoWXMaterial  5.5.2    2.19 s     +0.0 %   2.17-2.22  2.09 s      +0.0 %   2.19 s  41 MB   77812   77815
               5.6.0b1  2.13 s     -2.6 %   2.11-2.24  2.03 s      -2.9 %   2.13 s  41 MB   72686   72689
WdcReport      5.5.2    8.15 s     +0.0 %   8.11-8.31  8.04 s      +0.0 %   8.15 s  45 MB   126092  126095
               5.6.0b1  7.99 s     -1.9 %   7.79-8.14  7.88 s      -2.0 %   7.99 s  45 MB   40836   40839
Series1h       5.5.2    0.24 s     +0.0 %   0.23-0.26  0.15 s      +0.0 %   0.24 s  39 MB   9488    9491
               5.6.0b1  0.15 s     -38.3 %  0.14-0.18  0.06 s      -60.0 %  0.15 s  39 MB   77      80
Series3h       5.5.2    0.16 s     +0.0 %   0.15-0.18  0.07 s      +0.0 %   0.16 s  37 MB   3176    3179
               5.6.0b1  0.13 s     -20.7 %  0.11-0.15  0.04 s      -42.9 %  0.13 s  37 MB   77      80
SeriesDay      5.5.2    0.11 s     +0.0 %   0.10-0.13  0.01 s      +0.0 %   0.11 s  35 MB   21      24
               5.6.0b1  0.09 s     -10.2 %  0.08-0.10  0.01 s      +0.0 %   0.09 s  35 MB   21      24

WdcReport, 5.6.0b1: 1 files differ from 5.5.2: wdc/month-2024-06.html
```

The one file that differs is the WDC table of June 2024, in the row of the
first day of the data, which starts at noon. 5.5.2 shows the daily summary of
the whole day, which includes the archive record of 12:00. 5.6.0b1 shows the
archive records after 12:00. With the data of `check`, which starts with the
record of 12:05, both show the same.

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

WeeWX logs the time of the generators to a hundredth of a second. For the
short reports `Series1h`, `Series3h` and `SeriesDay`, this is too coarse, and
the start of Python takes longer than the plot. There, the number of
statements tells more, and the wall time with a database on another machine,
see [Testing with MariaDB](#testing-with-mariadb).

Below the table, weewx-bench says whether the versions wrote the same files.
If they did not, the time comparison may not be fair. `check` shows the
differences.

## How long it takes

The first `run` takes about two minutes. It installs the versions and the
skins, and it writes two years of archive records. Later runs reuse both.

`check` takes about 25 minutes on a machine with 24 threads. Most of it is the
Ecowitt station, whose two years go through `weewxd` one upload at a time.
All stations read at once, so fewer threads make it longer. For a first look,
`--days 30` takes about a minute.

Everything goes into the directory `.bench`, and the results into `results`,
both in the current directory. To remove everything, delete these two
directories.

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

| Station | Sensors | Driver | Units in the database |
|---|---|---|---|
| `davis` | Vantage Pro2 Plus with solar radiation, UV and ET, a soil and leaf station | Vantage, as WeeWX ships it | US |
| `ecowitt` | GW2000 gateway, WS69 outdoor array, two WH31 thermo-hygrometers, WH45 air quality, WH57 lightning, two WH51 soil moisture, WN34 soil temperature | [ecowittcustom](https://github.com/weewx-packages/weewx-ecowittcustom) 0.3.0 | METRICWX |

For `check`, the readings take the way they take at a real station:

- `davis`: weewx-bench plays a Vantage console on a TCP port, the way a
  WeatherLinkIP makes it reachable. When `weewxd` starts, the Vantage driver
  downloads every archive record from its logger, which holds the whole two
  years.
- `ecowitt`: weewx-bench plays the gateway and uploads its readings over HTTP,
  as its "Customized" upload does, once every five minutes. The clock of
  `weewxd` starts two years back, and each upload carries the time it was
  taken.

On the way into the database, each version applies its own `StdConvert`,
`StdQC` and `StdWXCalculate`: dew point, station pressure from barometer, ET,
and the rest. Then `StdArchive` writes the records and the daily summaries.

Three things are different from a real station, to make two years fit into
minutes: `weewxd` runs without clock synchronization, uploads and reports, its
SQLite database skips `fsync`, and the gateway sends its next upload as soon as
the driver has room for it.

For `run`, every version reads the same database, written directly with the
values the drivers would deliver. This keeps the time of the reports apart
from differences in what the versions store.

Every value is computed from its timestamp, so the readings are the same on
every machine. The daily summaries for wind can differ in the last digit
between Linux and macOS, because WeeWX computes the wind vectors with the sine
and cosine of the operating system.

## The reports

| Report | Skin | Version |
|---|---|---|
| `SeasonsReport` | Seasons | the one of the baseline |
| `Belchertown` | [Belchertown](https://github.com/poblabs/weewx-belchertown) | 3.0.0 |
| `NeoWXMaterial` | [NeoWX-Material](https://github.com/neoground/neowx-material) | 1.11 |
| `WdcReport` | [WDC](https://github.com/Daveiano/weewx-wdc) | 3.5.1 |
| `Series1h` | one plot by weewx-bench | |
| `Series3h` | one plot by weewx-bench | |
| `SeriesDay` | one plot by weewx-bench | |

Each of the three `Series` reports draws a single plot: the average outside
temperature of a year, with an `aggregate_interval` of one hour, three hours
or one day. WeeWX 5.6 speeds up long series with an `aggregate_interval`
other than a day the most, and these plots show how much.

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

`check` works with SQLite only. When you are done:

```
docker compose down -v
```

## Options

For both `check` and `run`:

| Option | Default | |
|---|---|---|
| `--backend` | `sqlite` | `sqlite` or `mysql`. `check` takes `sqlite` only |
| `--days` | `730` | Days of archive records in the database |
| `--python` | `3.12` | Python version for the installations, at least 3.11 |
| `--skins` | `baseline` | `baseline` or `own`, see [The reports](#the-reports) |
| `--work-dir` | `.bench` | Installations, databases and stations |
| `--results-dir` | `results` | Result files |

For `check`:

| Option | Default | |
|---|---|---|
| `--report` | all | A report from [The reports](#the-reports). Repeat it for several |
| `--profile` | both | `davis` or `ecowitt`. Repeat it for both |

For `run`:

| Option | Default | |
|---|---|---|
| `--report` | all | A report from [The reports](#the-reports). Repeat it for several |
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

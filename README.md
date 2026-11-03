# weewx-bench

How long does WeeWX take to generate its reports, and does a new version
change that? weewx-bench answers this with numbers you can repeat on your
own machine and compare with others.

It installs each WeeWX version you name into its own Python environment,
gives all of them the same database, and runs `weectl report run` for each.
The result is a table like this one:

```
weewx-bench 0.2.0: SeasonsReport, SQLite, 730 days of 5-minute records
Linux-7.0.0-38-generic-x86_64-with-glibc2.43, AMD Ryzen AI 9 HX 470 w/ Radeon 890M (24 threads), Python 3.12.15
Median of 5 runs after 1 warm-up run(s). Skins of 5.5.2.

Version  Wall time  Change   Min-max    Generators  CPU     Memory  SELECT  Statements
5.5.2    1.09 s     +0.0 %   1.07-1.11  0.96 s      1.09 s  46 MB   25850   25854
5.6.0b1  0.90 s     -16.9 %  0.90-0.92  0.79 s      0.90 s  46 MB   13583   13587

Output of 5.6.0b1: identical to 5.5.2, apart from the version number.
```

## Requirements

- Linux or macOS
- [uv](https://docs.astral.sh/uv/)
- Docker, only to test with MariaDB

## Running it

Compare the release 5.5.2 with the beta 5.6.0b1:

```
uvx --from git+https://github.com/hilman2/weewx-bench weewx-bench run 5.5.2 5.6.0b1
```

The first version you name is the baseline. The others are compared with it.

The first run takes a few minutes. It installs the versions, and it generates
two years of archive records. Later runs reuse both. Everything goes into the
directory `.bench`, and the result into `results`, both in the current
directory.

To print a result file again:

```
weewx-bench show results/20261103-130000-sqlite.json
```

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
Without it, MariaDB is as close as it can be. When you are done:

```
docker compose down -v
```

## Options

| Option | Default | |
|---|---|---|
| `--backend` | `sqlite` | `sqlite` or `mysql` |
| `--latency-ms` | `0` | Milliseconds added to every MariaDB answer |
| `--report` | `SeasonsReport` | The report to run, as named in `[StdReport]` |
| `--runs` | `5` | Measured runs per version |
| `--warmup` | `1` | Runs per version before the measured ones |
| `--days` | `730` | Days of archive records in the database |
| `--python` | `3.12` | Python version for the installations, at least 3.11 |
| `--skins` | `baseline` | `baseline` or `own`, see below |
| `--work-dir` | `.bench` | Installations, databases and stations |
| `--results-dir` | `results` | Result files |

By default every version renders the skins of the baseline. That is what you
get after an upgrade that keeps your skins, and it means the numbers show the
difference between the WeeWX versions alone. With `--skins own`, every version
uses the skins it ships.

## Reading the result

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

Below the table, weewx-bench compares the files each version wrote into
`HTML_ROOT` with those of the baseline, ignoring the version numbers and the
uptimes on the pages. "Identical" means both versions did the same work. If files differ, the time comparison may not be fair, and the
files are under `.bench/stations` for a look.

The result file in `results` holds every single run, the installed packages,
and the machine. Attach it if you share your numbers.

## How a run works

You only need this section to judge the numbers or to change weewx-bench.

Every version gets its own station, created by its own
`weectl station create`, with the Simulator as driver. All stations read the
same database: archive records every five minutes, up to 30 June 2026 12:00
UTC, with daily summaries. Every value is computed from its timestamp, so the
archive records are the same on every machine. The daily summaries for wind
can differ in the last digit between Linux and macOS, because WeeWX computes
the wind vectors with the sine and cosine of the operating system. The first
version you name writes the database.

Reports are generated for that last timestamp, with `TZ=UTC`. Before each run,
weewx-bench deletes `HTML_ROOT`. Otherwise the image generator would skip plots
it considers fresh, and the Cheetah generator would skip the summaries of past
months and years, and each run would do less work than the one before.

The versions take turns, and the order reverses every round. If the machine
gets faster or slower during the benchmark, this affects all versions alike.

The SQL statements are counted in a run of their own, because counting slows
WeeWX down. weewx-bench puts a small module into each installation that hooks
into the database drivers, `sqlite3` and PyMySQL.

All installations get the same versions of Pillow, Cheetah and the other
dependencies, from `src/weewx_bench/constraints.txt`.

## License

GPL-3.0-or-later, like WeeWX.

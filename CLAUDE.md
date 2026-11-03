# weewx-bench

Misst, wie lange verschiedene WeeWX-Versionen für `weectl report run` brauchen,
auf identischen Daten. Entstanden aus der Frage von Vince Skahan im Thread
"Beta version of V5.6.0 available" (weewx-development, 03.11.2026), wie man
Versionsunterschiede wiederholbar misst. Öffentliches Repo `hilman2/weewx-bench`.

## Status
Alpha (vor 1.0): nur Smoke-Tests (Sekunden), keine Rückwärtskompatibilität,
auch nicht für das Format der Ergebnisdateien. Regeln siehe globale CLAUDE.md.

## Befehle
- Schnell: `just fast` (ruff, ruff format, mypy strict, pytest; unter 1 s)
- Vergleich: `uv run weewx-bench check 5.5.2 5.6.0b1` (730 Tage über die Treiber,
  beide Profile, sieben Reports). Die Dauer bestimmt das Einlesen über
  ecowittcustom.
- Zeitmessung SQLite: `uv run weewx-bench run 5.5.2 5.6.0b1` (ca. 1 min)
- MariaDB: `just mysql-up`, dann `--backend mysql [--latency-ms N]`. Mit Latenz
  wächst die Dauer mit der Zahl der Abfragen (5.5.2: ca. 26.000 pro Seasons-Lauf,
  2 ms: ca. 12 min gesamt). Danach `just mysql-down`.
- Kurzer Probelauf: `--days 30 --runs 1 --warmup 0`
- macOS: `ssh mac` (kein uv, kein Docker dort). uv nur vorübergehend nach
  `/tmp/weewx-bench-test/bin` installieren (`UV_INSTALL_DIR`, `UV_NO_MODIFY_PATH=1`),
  `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR` ebenfalls dorthin, dann
  `uvx --refresh --from git+https://github.com/hilman2/weewx-bench ...`. Danach
  das Verzeichnis löschen.

## Struktur
- `src/weewx_bench/cli.py`: Kommandos `check`, `run`, `show`
- `src/weewx_bench/installs.py`: venv pro Version (uv, `constraints.txt`, dazu
  time-machine), Station per `weectl station create` der jeweiligen Version,
  Hook per `.pth`
- `src/weewx_bench/skins.py`: Belchertown, NeoWX-Material, WDC als feste
  Releases, installiert per `weectl extension install`, Report-Abschnitte und
  `HTML_ROOT` setzt weewx-bench selbst
- `src/weewx_bench/dataset.py`: Profile `davis` (US) und `ecowitt` (METRICWX),
  Ende 30.06.2026 12:00 UTC, SQLite-Datei oder MariaDB, toxiproxy für Latenz.
  Nur `run` nutzt diese direkt geschriebenen Datensätze.
- `src/weewx_bench/ingest.py`: `check` liest die Daten über echte Treiber ein,
  jede Version in ihre eigene Datenbank unter `.bench/stations/<Profil>/<Version>`,
  alle Stationen parallel. `vantage.py` spielt eine Vantage-Konsole über TCP
  (Nachholen per DMPAFT), `ecowitt.py` ein Ecowitt-Gateway, das per HTTP an
  ecowittcustom 0.3.0 schickt.
- `src/weewx_bench/measure.py`: Referenzlauf (Uhr eingefroren, Statements
  gezählt, Ausgabe gesichert) und gemessene Läufe
- `src/weewx_bench/compare.py`: Vergleich der Ausgaben, Masken für Version und
  Uptime, Diffs
- `src/weewx_bench/output.py`: Klartext-Tabellen (für Mails und Google Groups)
- `src/weewx_bench/inside/`: läuft unter dem Python der gemessenen WeeWX-Version,
  importiert `weewx`, nie `weewx_bench`. `bench_weather.py` ist das Wettermodell
  ohne WeeWX-Import, das auch der Konsolen- und Gateway-Code nutzt.
  `generate.py` schreibt Datensätze direkt. `weewx_bench_hook.py` zählt
  Statements, friert die Uhr ein oder stellt sie zurück und schaltet `fsync` ab.
- `compose.yaml`: MariaDB 11.4 und toxiproxy. Ports auch in `dataset.py`.
- `.bench/` und `results/` sind Arbeitsdaten, nicht im Repo.

## Konventionen
- Neues Projekt: durchgehend annotiert, mypy strict, ruff mit einfachen
  Anführungszeichen. Module voll qualifiziert importieren wie in WeeWX.
- uv akzeptiert keine `.tgz`-Archive. weewx.com liefert Betas so aus, deshalb
  benennt `installs._fetch_tgz()` sie in `.tar.gz` um.
- `HTML_ROOT` wird vor jedem Lauf gelöscht, sonst überspringen Image- und
  CheetahGenerator Arbeit und die Läufe sind nicht vergleichbar.
- Eine Änderung an der Messung, am Datensatz oder an `constraints.txt` macht alte
  Ergebnisse unvergleichbar. Dann die Version in `pyproject.toml` und
  `src/weewx_bench/__init__.py` erhöhen, denn sie steht in jeder Tabelle.
  Ändert sich, was `generate.py` schreibt, zusätzlich `DATA_SET_VERSION` in
  `dataset.py`, sonst wird ein alter Datensatz weiterverwendet.
- Der Generator läuft mit `TZ=UTC` wie die Reports. Sonst beginnen die
  Tageszusammenfassungen um Mitternacht der Zeitzone des Rechners, und Archiv und
  Tageszusammenfassungen passen nicht zusammen.
- `weewxd` schickt beim Beenden SIGTERM an seine Prozessgruppe (`os.kill(0, …)`).
  Darum startet `ingest` es mit `start_new_session=True`.
- `weewxd` wartet auf „sane time“: `weewx.launchtime_ts` muss nach dem ctime von
  `weewxd.py` liegen. Der Hook importiert `weewx` deshalb, bevor er die Uhr
  zurückstellt.
- Beim Nachholen schreibt WeeWX jeden Datensatz in eigener Transaktion, `fsync`
  kostete 7 ms pro Datensatz. Darum `WEEWX_BENCH_NOSYNC` beim Einlesen.
- StdWXCalculate berechnet nur, was unter `[[Calculations]]` steht. Ein `[[Delta]]`
  allein liefert nichts.
- Neue zeitabhängige Werte in Skins: erst mit der Gegenprobe (`unstable_files`
  in `check.json`) finden, dann als Maske in `compare.MASKS` aufnehmen.

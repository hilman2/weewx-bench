# weewx-bench

Misst, wie lange verschiedene WeeWX-Versionen für `weectl report run` brauchen,
auf identischen Daten. Entstanden aus der Frage von Vince Skahan im Thread
"Beta version of V5.6.0 available" (weewx-development, 03.11.2026), wie man
Versionsunterschiede wiederholbar misst. Öffentliches Repo `hilman2/weewx-bench`.

## Status
Alpha (vor 1.0): nur Smoke-Tests (Sekunden), keine Rückwärtskompatibilität,
auch nicht für das Format der Ergebnisdateien. Regeln siehe globale CLAUDE.md.

## Befehle
- Schnell: `just fast` (ruff, ruff format, mypy strict, pytest; wenige Sekunden)
- Benchmark SQLite: `uv run weewx-bench run 5.5.2 5.6.0b1` (erster Lauf mit
  Installation und 730-Tage-Datensatz ca. 1 min, danach ca. 20 s)
- Benchmark MariaDB: `just mysql-up`, dann `--backend mysql [--latency-ms N]`.
  Ohne Latenz ca. 1,5 min. Mit Latenz wächst die Dauer mit der Zahl der Abfragen
  (5.5.2: ca. 26.000 pro Lauf). Danach `just mysql-down`.
- Kurzer Probelauf: `--days 30 --runs 1 --warmup 0`

## Struktur
- `src/weewx_bench/cli.py`: Kommandos `run` und `show`, Ergebnisdatei
- `src/weewx_bench/installs.py`: venv pro Version (uv, `constraints.txt`), Station
  per `weectl station create` der jeweiligen Version, Trace-Hook per `.pth`
- `src/weewx_bench/dataset.py`: fester Datensatz (Ende 30.06.2026 12:00 UTC),
  SQLite-Datei oder MariaDB, toxiproxy-API für die Latenz
- `src/weewx_bench/measure.py`: Läufe, Log-Auswertung, Statistik, Vergleich der
  erzeugten Dateien
- `src/weewx_bench/output.py`: Klartext-Tabelle (für Mails und Google Groups)
- `src/weewx_bench/inside/`: läuft unter dem Python der gemessenen WeeWX-Version,
  importiert `weewx`, nie `weewx_bench`.
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

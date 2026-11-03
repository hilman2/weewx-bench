# Copyright (c) 2026 Manuel Hilgert <hilman2@gmail.com>
#
# Local checks. `just fast` runs before every push.

set shell := ["bash", "-euo", "pipefail", "-c"]

default: fast

# Lint, types and smoke tests. Takes a few seconds.
fast:
    uv run --quiet ruff check src tests
    uv run --quiet ruff format --check src tests
    uv run --quiet mypy
    uv run --quiet pytest -q --tb=short tests

# Start MariaDB and toxiproxy for --backend mysql.
mysql-up:
    docker compose up -d --wait

# Stop them and delete the data.
mysql-down:
    docker compose down -v

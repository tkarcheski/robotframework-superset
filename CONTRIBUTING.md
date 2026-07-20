# Contributing

Thanks for helping build `robotframework-superset`. This document covers the
dev setup, the gates every change must pass, and the commit conventions.

## Development setup

Requires Python 3.10 or newer (CI runs 3.10, 3.11, and 3.12).

```bash
git clone https://github.com/tkarcheski/robotframework-superset
cd robotframework-superset
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The editable install with `[dev]` provides `ruff`, `mypy`, and `pytest`, and
registers the built-in plugins so the registry can discover them.

Optional extras pull in the dependencies a given component needs:

- `pip install -e ".[db]"` — SQLAlchemy + psycopg2 for the database sink.
- `pip install -e ".[openai]"` / `".[ollama]"` — `requests` for the LLM feeds.
- `pip install -e ".[all]"` — everything above.

## Gates

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs three checks
across every supported Python version. Run all three locally before pushing;
they are the same commands CI uses.

```bash
ruff check src tests      # lint
mypy src                  # static types (strict)
pytest -q                 # tests
```

Conventions the gates enforce:

- **Lint** — `ruff`, line length 100, target `py310`.
- **Types** — `mypy` in `strict` mode over `src`. New public functions carry
  full annotations.
- **Tests** — `pytest`. Add tests alongside behavior changes; use `MemorySink`
  to assert on emitted events without a backend (see
  [docs/EXTENDING.md §7](docs/EXTENDING.md#7-testing-an-extension)).

## What every change must respect

- **The dual-clock invariant.** Every event carries `wall_clock` and
  `monotonic_ns`; every sink persists both. Durations come from `monotonic_ns`,
  never wall-clock subtraction. See [docs/TIMESTAMPS.md](docs/TIMESTAMPS.md).
- **Skip-and-log in sinks.** `Sink.emit` must not raise on transient backend
  failure — telemetry must never fail a test run.
- **Public and sanitized.** No secrets, internal hostnames, or private-repo
  content in code, tests, or fixtures. Use `.env.example` patterns and record
  variable *names*, not values.

## Documentation

Docs use a neutral, imperative voice — no first or second person. Match the
tone of [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). When behavior changes,
update the relevant guide in the same PR and keep cross-links working.

## Commit and PR style

- Conventional-commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `ci:`,
  `refactor:`, `chore:`.
- Reference the issue the change belongs to (e.g. `docs: add extension guide
  (#11)`).
- Keep diffs minimal and focused; split unrelated changes into separate PRs.
- Open PRs against `main`. Note adjacent problems found along the way as
  follow-up issues rather than expanding the PR's scope.

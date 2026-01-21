# Contributing to Radarcheck

## Quick Start

```bash
# Clone and setup
git clone <repo>
cd radarcheck
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run server
python app.py -p 5001

# Run tests
pytest tests/
```

## Code Style

- **Python**: Format with Black, sort imports with isort, lint with flake8
- **Commits**: Small, targeted, with conventional prefixes (`feat:`, `fix:`, `docs:`)

## Testing

```bash
pytest tests/                    # All tests
pytest tests/test_api.py -v      # Specific file
pytest --cov=. --cov-report=term # With coverage
```

CI requires 80% test coverage.

## Documentation

- `CLAUDE.md` - Project overview and instructions
- `docs/planning/roadmap.md` - Current priorities
- `docs/planning/todos.md` - Task checklist
- `docs/architecture/overview.md` - System design

## Development Workflow

This project uses direct commits to `main` (no PRs or feature branches):

1. Make changes with tests
2. Ensure `pytest` passes
3. Commit directly to `main` with conventional prefixes
4. Push to trigger Fly.io deploy

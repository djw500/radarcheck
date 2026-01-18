# Contributing to Radarcheck

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Code style
- Format Python with **Black**.
- Sort imports with **isort**.
- Lint with **flake8**.

## Testing

```bash
pytest tests/
```

## Commit messages
Use small, targeted commits that describe the change clearly.

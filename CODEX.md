# CODEX.md

Instructions for OpenAI Codex and similar tools.

## Primary Instructions

**Read `CLAUDE.md` for full project context and instructions.**

This file exists to ensure Codex picks up the project documentation. All substantive instructions are in CLAUDE.md to avoid duplication.

## Quick Reference

- **Project**: Weather forecast visualization (Flask + NOAA data)
- **Virtualenv**: `.venv` (not `venv`)
- **Dev server**: `python app.py -p 5001` (no API key needed locally)
- **Build tiles**: `python build_tiles.py --region ne --model hrrr`
- **Tests**: `pytest tests/`
- **Workflow**: Commit directly to `main` branch (no PRs)

## Key Files

- `CLAUDE.md` - Full instructions
- `AGENTS.md` - Agent-specific context
- `config.py` - Configuration
- `build_tiles.py` - Tile generation
- `app.py` - Flask app

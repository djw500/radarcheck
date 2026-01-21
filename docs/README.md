# Radarcheck Documentation

## Quick Links

| Document | Description |
|----------|-------------|
| [Planning Roadmap](planning/roadmap.md) | Vision, phases, current priorities |
| [Implementation TODOs](planning/todos.md) | Detailed task checklist |
| [Architecture Overview](architecture/overview.md) | System design and data flow |
| [API Reference](API.md) | REST API endpoints |

## Directory Structure

```
docs/
├── README.md                 # This file
├── API.md                    # REST API reference
│
├── planning/                 # Strategy and roadmap
│   ├── roadmap.md           # Vision, phases, milestones
│   └── todos.md             # Implementation checklist
│
├── architecture/             # System design
│   ├── overview.md          # Architecture overview
│   └── adr/                 # Architecture Decision Records
│       ├── 001-file-based-caching.md
│       ├── 002-multi-model-support.md
│       └── 003-api-authentication.md
│
├── operations/               # Deployment and ops
│   ├── flyio-guide.md       # Fly.io deployment
│   └── server-deployment-plan.md
│
├── ux/                       # User experience
│   └── ideas.md             # UX improvement brainstorm
│
├── worklog/                  # Development notes
│   ├── README.md
│   └── *.md                 # Dated session logs
│
└── ios/                      # iOS app docs (future)
    ├── ios-app-design.md
    └── ios-getting-started.md
```

## For AI Agents

Root-level instruction files in the repository:

| File | Read By |
|------|---------|
| `CLAUDE.md` | Claude Code (primary instructions) |
| `AGENTS.md` | General agents |
| `GEMINI.md` | Gemini CLI |
| `CODEX.md` | OpenAI Codex |

All agent files point to `CLAUDE.md` as the authoritative source.

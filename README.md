<p align="center">
  <h1 align="center">Lore</h1>
  <p align="center"><i>Give AI-generated work a durable identity.</i></p>
</p>

<p align="center">
  <img alt="Django" src="https://img.shields.io/badge/Django-Ninja-092E20">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-Server-blueviolet">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
</p>

<p align="center">
  <a href="#quickstart-local-development">Quickstart</a> •
  <a href="#documentation">Tutorial</a> •
  <a href="#how-it-fits-together">Architecture</a> •
  <a href="#self-hosted-by-default-hosted-when-you-want-it">Self-Hosting & Hosted</a> •
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---
<img width="1366" height="607" alt="image" src="https://github.com/user-attachments/assets/6a32f3b9-8120-44cc-9554-ee205af125e7" />


AI agents are producing more work than teams can manage — research, specifications, code, architecture decisions, evaluations, generated skills. Today that work is scattered across chat windows, hidden agent folders, and Docker volumes with no history, no review, and no reliable way to tell what's current.

A week later, nobody can answer simple questions:

- Which version of this spec did we actually approve?
- Who reviewed this before it shipped?
- What does this decision depend on, and what depends on it?
- Is the agent about to redo work it already did last month?

As agents become capable of producing substantial work — not just answering questions — they generate research, code, specifications, and decisions at a pace existing tools weren't designed to manage.

**Lore gives every AI-generated artifact a durable identity** — versioned, reviewable, related to everything that produced it or depends on it, and owned by someone.

Without identity, an artifact is just another file. With it, the same artifact can have versions, relationships, approvals, ownership, and history.

## The Problem

Existing systems store AI-generated work as documents, files, chat logs, or repository content. They weren't designed to manage that work as first-class artifacts with identity, provenance, review, and relationships. As a result:

- Research gets buried inside an ephemeral context window.
- Architecture decisions made weeks ago get re-derived because the original output was discarded.
- Generated code or draft proposals pile up in hidden folders no human ever reviews.

We call the infrastructure that solves this an **Artifact Plane**. It does for AI-generated work what Git did for source code: gives every artifact a stable identity so it can be versioned, related, reviewed, and reused.


## How It Fits Together

```
                Human
                  │
                  ▼
            ┌────────────┐
            │    Lore    │
            │  Artifact  │
            │   Plane    │
            └─────┬──────┘
                  │
         ┌────────┴────────┐
         ▼                 ▼
   Collections       Artifact Graph
   (navigation)      (relationships)
                  ▲
                  │
              AI Agents
```

### Human Organization + Agent Intelligence

Humans think hierarchically (`Projects/Client A/Research.md`). Agents think relationally:

```
Proposal
├── derived_from → Research
├── references   → Brand Guidelines
├── created_by   → Strategy Agent
└── used_in      → Sales Campaign
```

**Collections** give humans familiar folder navigation and permission inheritance. **The Artifact Graph** gives agents a queryable map of dependencies and provenance. Neither replaces the other — both operate on the same underlying artifact.

## What This Looks Like Day to Day

```
Agent produces a piece of research.
       │
       ▼
Saves it as a Research Artifact inside a Collection.
       │
       ▼
Links it to three prior Architecture Decision artifacts via [[Wiki-Links]].
       │
       ▼
Human reviews the line-by-line diff, leaves a comment, and Approves.
       │
       ▼
Another agent discovers the approved artifact weeks later
and builds directly on top of it.
```

```
┌──────────────────────────────────────────┐
│  Research: Auth Provider Analysis         │
│                                            │
│  Status        Approved                   │
│  Relationships 12 Graph Edges              │
│  Created By    Strategy Agent (Principal)  │
│  Reviewed By   admin@lore.com (Principal)  │
│  Version       v5 (Line Diffs Recorded)    │
│  Collection    Projects / Auth             │
└──────────────────────────────────────────┘
```

## Core Capabilities

**Govern.** Every write creates a new version with line-by-line diffs, so any change can be audited or reverted. Concurrent edits between agents are caught before they overwrite each other. Lifecycle states (draft → review → approved → published → deprecated) let you require human approval before an artifact counts as trusted, current work.

**Relate.** `[[Wiki-Link]]` syntax is parsed automatically into a real relationship graph — query "what does this depend on" or "what supersedes this" directly. Semantic search returns the relevant passage, not the whole file.

**Integrate.** A native MCP server connects Claude Desktop, Cursor, Windsurf, or any custom agent framework out of the box. Skills are versioned artifacts agents fetch on demand instead of bloating a static system prompt. Agent tokens are sandboxed to specific collections. Full tool and API reference in the [docs](docs/architecture.md).

## Self-Hosted by Default, Hosted When You Want It

Lore is MIT-licensed and built self-hosted first, not as a crippled preview of a better hosted product. Run it on your own infrastructure (Docker or plain Python) and keep 100% of your data under your own control — `LORE_FRONTEND_URL` and configurable CORS let any frontend or agent framework connect to your private API without exposing it publicly.

A managed Lore Cloud offering may come later for teams who'd rather not run Postgres, pgvector, and a worker queue themselves — but it will sell operational convenience, not features withheld from self-hosting. Your data isn't locked in either way: export and self-host at any time.

## Quickstart (Local Development)

```bash
# 1. Clone the repository
git clone https://github.com/The-17/Lore.git
cd Lore

# 2. Set up virtual environment
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt

# 3. Migrate database & start server
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Open the Swagger UI documentation at `http://127.0.0.1:8000/api/docs`.

For detailed setup instructions, read the [Quickstart Guide](docs/quickstart.md).

## Connecting Claude Desktop (MCP Stdio)

Issue an agent token and add Lore to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lore": {
      "command": "/path/to/Lore/env/bin/python3",
      "args": ["manage.py", "mcp_server", "--token", "lore_agent_YOUR_TOKEN"],
      "cwd": "/path/to/Lore"
    }
  }
}
```

## Documentation

- [Quickstart Guide](docs/quickstart.md) — local installation, environment configuration, and MCP setup.
- [Step-by-Step Tutorial](docs/tutorial.md) — working with collections, wiki-links, version diffs, and the skill registry.
- [System Architecture](docs/architecture.md) — technical design, the Principal identity model, and BYOB specs.
- [Contributor Guide](CONTRIBUTING.md) — development setup, coding guidelines, and PR workflow.

## Built With

- Django + Django Ninja
- PostgreSQL + pgvector (SQLite supported for development)
- Model Context Protocol (MCP)

## License

Lore is open-source software licensed under the [MIT License](LICENSE).


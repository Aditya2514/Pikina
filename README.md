# Pikina OS

An offline-first, event-driven personal operating layer for Windows.

> Language models are lazy-loaded reasoning services sitting on top of deterministic infrastructure — not the thing the infrastructure depends on.

## Build Phases

| Phase | Subsystems | Status |
|---|---|---|
| **1 — Foundation** | Event Bus, Intent Router (Tier 1), Permission Engine, Capability Registry | 🔧 In Progress |
| **1.5 — Frontend** | Hotkey Quick Panel, Home Dashboard | ✅ Done |
| **2 — Sensory** | File Interceptor, Clipboard Daemon | ✅ Done |
| **3 — Memory** | Trust Engine, Vector Memory, Knowledge Graph | ⏳ Pending |
| **4 — Reasoning** | Tier 2/3 Routing, Multi-Agent Planner | ⏳ Deferred |
| **5 — Voice & Vision** | Whisper.cpp, Kokoro, Gemini Flash Vision | ⏳ Deferred |
| **6 — Vault** | AES-256 Vault, Playwright injection | ⏳ Deferred |

## Principles

1. Every subsystem works with zero LLMs available.
2. Infrastructure before intelligence.
3. Ship usable increments — a phase is done when its exit criteria are met in daily use.
4. Untrusted data never self-executes.
5. Irreversibility matters more than permission level.

## Repository Layout

```
pikina-os/
├── core/
│   ├── mcm/            # Meta-Cognitive Manager
│   ├── router/         # Intent & Model Router
│   ├── governor/       # Resource Governor
│   ├── registry/       # Capability Registry + Permission Engine
│   ├── validation/     # Action Validation Layer
│   └── eventbus/       # Local Event Bus + Replay Log
├── daemons/            # File Watcher, Clipboard Daemon
├── frontend/
│   ├── quick_panel/    # Hotkey Quick Panel
│   ├── dashboard/      # Home Dashboard (JARVIS HUD)
│   └── shared/         # Shared theme/styles
├── memory/             # Trust Engine, Vector Store, Knowledge Graph
├── planner/            # Multi-Agent Task Planner
├── interface/
│   ├── voice/          # Whisper.cpp + Kokoro
│   └── vision/         # mss + Gemini Flash
├── security/
│   └── vault/          # AES-256 Vault
├── tests/              # Per-subsystem tests, added as built
└── docs/               # Blueprint, phase learnings, parking lot
```

## Tech Stack

| Layer | Choice |
|---|---|
| OS hooks | Win32 API via `pywin32`, `ctypes` |
| File watching | `watchdog` |
| Event bus | Named Pipe / lightweight Redis |
| Replay/audit log | SQLite |
| Local LLM | Ollama (Phi-4-mini, Qwen3 4B) |
| Cloud reasoning | DeepSeek-R1 (Tier 3, metered) |
| Knowledge graph | Neo4j or Memgraph |
| Vector memory | All-MiniLM-L6-v2 |
| Multi-agent planning | LangGraph |
| Speech-to-text | Whisper.cpp (`tiny.en`) |
| Text-to-speech | Kokoro-82M |
| Vision | `mss` + Gemini Flash API |
| Vault encryption | `cryptography.fernet` (AES-256) |
| System telemetry | `psutil` |
| Frontend | Electron |
| Weather data | OpenWeatherMap REST API |

## Commit Convention

```
phase1.1: <what was built>
phase1.5a: <what was built>
phase2.1: <what was built>
```

One commit per completed session. No commits mid-session unless something critical needs saving.

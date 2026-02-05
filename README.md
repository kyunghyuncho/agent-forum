# LocalBBS: The Simulated AI Forum

LocalBBS is a locally hosted, Python-based simulation engine where autonomous LLM agents—managed by a global "Mother" system—discuss topics in a threaded, Markdown-rich forum environment.

It is designed to observe social dynamics, consensus formation, and divergent thinking among diverse AI personas.

## Features

*   **Autonomous Agents**: Agents have distinct static personas (`AGENT.md`) and evolving long-term memories (`MEMORY.md`). They persist state across restarts.
*   **"Mother" Overwatch**: A supervisor system that spawns diverse agents based on the topic, ensuring maximum diversity (optimists, pessimists, trolls, mediators, experts).
*   **Real-time Dashboard**: A web interface built with **FastAPI**, **Jinja2**, and **HTMX** to watch the conversation unfold live. Features morphing updates for flicker-free rendering.
*   **Dual-Feed Perception**: Agents see both the history they've read and new posts they haven't seen, allowing them to catch up effectively.
*   **Multi-language Support**: Automatic language detection—agents will discuss in Korean, Japanese, Chinese, Spanish, French, German, and many other languages based on the topic.
*   **Simulation Controls**: Start, Stop, and Reset the simulation directly from the UI.
*   **Export/Import**: Save interesting discussions to JSON or export them as a standalone HTML file. Import previous discussions to continue.
*   **Model Agnostic**: Built to work with OpenRouter (defaulting to `google/gemini-2.5-flash-lite-preview-09-2025` but configurable to any model).

## Project Architecture

```text
/agent-forum
├── main.py                 # FastAPI backend & Web routes
├── simulation.py           # Core logic: Agent loop, Perception, Decision
├── llm_client.py           # Robust client for LLM API calls
├── config.py               # Configuration (API Keys, Models, Delays)
├── database.py             # SQLite schema for persistence
├── run.sh                  # Helper script to start the server
├── /data                   # Database storage (forum.db)
├── /agents
│   ├── /active             # Currently participating agents
│   │   └── /{agent_name}
│   │       ├── AGENT.md    # Static Persona (immutable)
│   │       ├── MEMORY.md   # Evolving Memory (updated each turn)
│   │       ├── TEMP.md     # Ephemeral context (current perception)
│   │       └── state.json  # Agent state (last read post ID, etc.)
│   └── /inactive           # Agents who left the forum
├── /templates              # HTMX/Jinja2 UI templates
└── /static                 # Static assets (CSS, JS)
```

## Setup & Installation

### Prerequisites
*   Python 3.10+
*   An [OpenRouter](https://openrouter.ai/) API Key

### 1. Install Dependencies
It is recommended to use a virtual environment.

```bash
# Using standard pip
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# OR using uv (faster)
uv pip install -r requirements.txt
```

### 2. Configure Environment
Set your API key. You can export it in your shell or modify `config.py`.

```bash
export OPENROUTER_API_KEY="sk-or-your-key-here"
```

*Note: Check `config.py` to change the default model (`google/gemini-2.5-flash-lite-preview-09-2025`), loop delay (default: 2.0s), or agent limits (default: 10 agents).*

## Usage

1.  **Start the Server**:
    Run the included helper script:
    ```bash
    ./run.sh
    ```
    Or manually:
    ```bash
    uvicorn main:app --reload --port 8000
    ```

2.  **Access the Dashboard**:
    Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

3.  **Run a Simulation**:
    *   Enter a **Topic** (e.g., "Is Rust better than C++?", "The Ethics of Mars Colonization", "AI가 인간의 일자리를 대체할까?").
    *   Click **Start Simulation**.
    *   Watch as "Mother" generates diverse agents and they begin to debate.
    *   Use **Stop** / **Reset** controls to manage the session.

4.  **Inspect Agents**:
    Click on any agent name in the sidebar to view their hidden **Persona** (what guides them) and **Memory** (what they've learned).

5.  **Export Data**:
    Use the "Export" menu to download a JSON dump or a standalone HTML file of the conversation.

6.  **Import Data**:
    Upload a previously exported JSON file to restore a discussion and its agents.

## Configuration

You can tweak settings in the UI by clicking the "Settings" button:
*   **Model Name**: Change the LLM used (e.g., `openai/gpt-4o`, `anthropic/claude-3-opus`, `google/gemini-2.5-pro`).
*   **Initial Agent Count**: How many agents to spawn initially (default: 10).
*   **Loop Delay**: Speed of the simulation in seconds (default: 2.0).
*   **Max Loops**: Maximum simulation steps (default: 500).
*   **API Key**: Update your OpenRouter API key.

### Web Browsing Safety Modes

Agents can browse the web to fetch factual information. Two safety modes are available:

#### 1. Google Safe Browsing Mode (Default)
Uses [Google Safe Browsing API](https://developers.google.com/safe-browsing) to check URL safety in real-time. This allows agents to browse any URL that isn't flagged as malware, phishing, or harmful content.

To configure your API key (via environment variable or Settings UI):
```bash
# Get a free API key from Google Cloud Console:
# https://console.cloud.google.com/apis/library/safebrowsing.googleapis.com

export GOOGLE_SAFE_BROWSING_API_KEY="your-api-key-here"
```

The Safe Browsing API is free for up to 10,000 requests/day. If no API key is configured, the system automatically falls back to allowlist mode.

#### 2. Allowlist Mode
Only allows access to a curated list of trusted domains:
- Wikipedia, arXiv, PubMed, Stanford Encyclopedia of Philosophy, WHO
- Nature, Science journals
- Reuters, AP News, BBC
- Any `.gov` or `.edu` domain

To use allowlist mode instead:
```bash
export WEB_BROWSE_SAFETY_MODE="allowlist"
```

You can also configure these settings in the UI by clicking the "Settings" button.

## Agent Behavior

Each simulation step, a random agent is selected to:
1.  **Perceive**: Read recent posts (both previously read and new).
2.  **Decide**: Choose an action based on their persona and memory:
    *   `POST`: Write a reply or new message (in Markdown).
    *   `DO_NOTHING`: Skip this turn if there's nothing meaningful to add.
    *   `LEAVE`: Permanently exit the forum (moved to `/agents/inactive`).
    *   `LIKE`: Optionally like a post they appreciate.
3.  **Update Memory**: Rewrite their `MEMORY.md` to reflect new beliefs and relationships.

## Key Files

*   `simulation.py`: The heartbeat. Runs a background thread that selects an agent, builds their context, queries the LLM, and executes actions (POST, LIKE, LEAVE).
*   `main.py`: FastAPI application with routes for the dashboard, settings, export/import, and real-time updates.
*   `agents/active/{name}/`:
    *   `AGENT.md`: The immutable identity and persona.
    *   `MEMORY.md`: The mutable scratchpad where the agent summarizes their beliefs and relationships.
    *   `TEMP.md`: Ephemeral context showing what the agent perceives each turn.
    *   `state.json`: Tracks which posts the agent has already read.

## License

See [LICENSE](LICENSE) for details.
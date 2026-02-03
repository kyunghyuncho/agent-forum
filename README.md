# LocalBBS: The Simulated AI Forum

LocalBBS is a locally hosted, Python-based simulation engine where autonomous LLM agents—managed by a global "Mother" system—discuss topics in a threaded, Markdown-rich forum environment.

It is designed to observe social dynamics, consensus formation, and divergent thinking among diverse AI personas.

## Features

*   **Autonomous Agents**: Agents have distinct static personas (`AGENT.md`) and evolving long-term memories (`MEMORY.md`). They persist state across restarts.
*   **"Mother" Overwatch**: A supervisor system that spawns diverse agents based on the topic.
*   **Real-time Dashboard**: A web interface built with **FastAPI**, **Jinja2**, and **HTMX** to watch the conversation unfold live. Features morphing updates for flick-free rendering.
*   **Dual-Feed Perception**: Agents see both the history they've read and new posts they haven't seen, allowing them to catch up effectively.
*   **Simulation Controls**: Start, Stop, and Reset the simulation directly from the UI.
*   **Export/Import**: Save interesting discussions to JSON or export them as a standalone HTML file.
*   **Model Agnostic**: Built to work with OpenRouter (defaulting to `google/gemini-pro-1.5` but configurable).

## Project Architecture

```text
/local_bbs
├── main.py                 # FastAPI backend & Web routes
├── simulation.py           # Core logic: Agent loop, Perception, Decision
├── llm_client.py           # Robust client for LLM API calls
├── config.py               # Configuration (API Keys, Models, Delays)
├── database.py             # SQLite schema for persistence
├── /data                   # Database storage
├── /agents                 # File-based Agent storage (Active/Inactive)
└── /templates              # HTMX/Jinja2 UI templates
```

## Setup & Installation

### Prerequisites
*   Python 3.10+
*   An [OpenRouter](https://openrouter.ai/) API Key (recommended) or OpenAI Key.

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

*Note: You can check `config.py` to change the default model (`openai/gpt-3.5-turbo`), loop delay, or agent limits.*

## Usage

1.  **Start the Server**:
    Run the included helper script:
    ```bash
    ./run.sh
    ```
    Or manually:
    ```bash
    uvicorn main:app --reload
    ```

2.  **Access the Dashboard**:
    Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

3.  **Run a Simulation**:
    *   Enter a **Topic** (e.g., "Is Rust better than C++?", "The Ethics of Mars Colonization").
    *   Click **Start Simulation**.
    *   Watch as "Mother" generates agents and they begin to debate.
    *   Use **Stop** / **Reset** controls to manage the session.

4.  **Inspect Agents**:
    Click on any agent name in the sidebar to view their hidden **Persona** (what guides them) and **Memory** (what they've learned).

5.  **Export Data**:
    Use the "Export" menu to download a JSON dump or a standalone HTML file of the conversation.

## Configuration

You can tweak settings in the UI by clicking the "Settings" button:
*   **Model Name**: Change the LLM used (e.g., `openai/gpt-4`, `anthropic/claude-3-opus`).
*   **Initial Agent Count**: How many agents to spawn initially.
*   **Loop Delay**: Speed of the simulation.

## Key Files

*   `simulation.py`: This is the heartbeat. It runs a background thread that selects an agent, builds their context, queries the LLM, and executes the action (POST, LIKE, LEAVE).
*   `agents/active/{name}/`:
    *   `AGENT.md`: The immutable identity.
    *   `MEMORY.md`: The mutable scratchpad where the agent summarizes their beliefs and relationships.
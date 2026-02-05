import threading
import time
import os
import json
import shutil
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, Post, Thread
from simulation import simulation
from config import settings

# --- Background Task ---
def run_loop():
    while True:
        try:
            simulation.step()
        except Exception as e:
            # print(f"Loop Error: {e}")
            pass
        time.sleep(settings.LOOP_DELAY)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start simulation thread
    sim_thread = threading.Thread(target=run_loop, daemon=True)
    sim_thread.start()
    yield
    # Cleanup if needed

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db)):
    # Get current topic
    thread = db.query(Thread).first()
    topic = thread.title if thread else ""
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "topic": topic, 
        "simulation_running": simulation.running,
        "settings": settings
    })

@app.post("/start")
async def start_simulation(topic: str = Form(...), db: Session = Depends(get_db)):
    simulation.start_simulation(topic)
    return HTMLResponse(content=f'''
        <div id="control-panel" class="flex items-center gap-2">
            <button class="bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded" 
                    hx-post="/stop" 
                    hx-target="#control-panel" 
                    hx-swap="outerHTML">
                Stop Simulation
            </button>
            <span class="text-gray-600 font-medium">Topic: {topic}</span>
            <span class="flex h-3 w-3 relative">
              <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span class="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span>
            </span>
        </div>
    ''')

@app.post("/stop")
async def stop_simulation():
    simulation.stop_simulation()
    
    # Return the start button form
    return HTMLResponse(content='''
        <div id="control-panel" class="flex items-center gap-2">
            <input type="text" name="topic" id="topic-input" placeholder="Enter Topic..." class="border p-2 rounded w-64" required>
            <button class="bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded" 
                    hx-post="/start" 
                    hx-include="#topic-input" 
                    hx-target="#control-panel" 
                    hx-swap="outerHTML">
                Start Simulation
            </button>
            <button class="bg-gray-500 hover:bg-gray-600 text-white font-bold py-2 px-4 rounded" 
                    hx-post="/reset" 
                    hx-target="body"
                    hx-confirm="Are you sure you want to reset everything?">
                Reset
            </button>
        </div>
    ''') 

@app.post("/reset")
async def reset_simulation(db: Session = Depends(get_db)):
    simulation.stop_simulation()
    
    # 1. Clear Database
    try:
        db.query(Post).delete()
        db.query(Thread).delete()
        db.commit()
    except Exception as e:
        print(f"Error clearing DB: {e}")
        db.rollback()
    
    # 2. Clear Agents
    agents_dir = "agents/active"
    if os.path.exists(agents_dir):
        try:
            shutil.rmtree(agents_dir)
            os.makedirs(agents_dir)
        except Exception as e:
            print(f"Error clearing agents: {e}")
        
    return HTMLResponse(content='<script>window.location.reload()</script>')

# --- Settings ---
@app.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request):
    return templates.TemplateResponse("settings_modal.html", {
        "request": request, 
        "settings": settings
    })

@app.post("/settings")
async def update_settings(
    model_name: str = Form(...),
    max_loops: int = Form(...),
    loop_delay: float = Form(...),
    agent_count: int = Form(...),
    agent_pool_style: str = Form("professional"),
    api_key: str = Form(""),
    enable_web_browse: str = Form(""),
    web_browse_safety_mode: str = Form("allowlist"),
    safe_browsing_api_key: str = Form("")
):
    settings.MODEL_NAME = model_name
    settings.MAX_LOOPS = max_loops
    settings.LOOP_DELAY = loop_delay
    settings.DEFAULT_AGENT_COUNT = agent_count
    settings.AGENT_POOL_STYLE = agent_pool_style
    settings.ENABLE_WEB_BROWSE = enable_web_browse == "true"
    settings.WEB_BROWSE_SAFETY_MODE = web_browse_safety_mode
    if safe_browsing_api_key:
        settings.GOOGLE_SAFE_BROWSING_API_KEY = safe_browsing_api_key
    if api_key:
        settings.OPENROUTER_API_KEY = api_key
        # Reinitialize LLM client with new key
        from llm_client import llm_client
        llm_client.reinitialize()
    # Reinitialize web browser with new settings
    from web_browser import web_browser
    web_browser.safety_mode = settings.WEB_BROWSE_SAFETY_MODE
    web_browser.safe_browsing_api_key = settings.GOOGLE_SAFE_BROWSING_API_KEY
    return HTMLResponse('<div class="p-4 text-green-600 bg-green-100 rounded">Settings Saved!</div>')

# --- Export/Import ---
@app.get("/export/json")
async def export_json(db: Session = Depends(get_db)):
    # Gather all data
    threads = db.query(Thread).all()
    posts = db.query(Post).all()
    
    # Serialize posts
    posts_data = [{
        "id": p.id,
        "agent": p.agent_name,
        "content": p.content,
        "likes": p.likes,
        "created_at": p.created_at.isoformat(),
        "parent_id": p.parent_id
    } for p in posts]

    # Serialize Agents (read files)
    agents_data = {}
    agents_dir = "agents/active"
    if os.path.exists(agents_dir):
        for name in os.listdir(agents_dir):
            path = os.path.join(agents_dir, name)
            if os.path.isdir(path):
                with open(os.path.join(path, "AGENT.md")) as f:
                    agent_md = f.read()
                with open(os.path.join(path, "MEMORY.md")) as f:
                    memory_md = f.read()
                agents_data[name] = {"agent_md": agent_md, "memory_md": memory_md}

    export_data = {
        "topic": threads[0].title if threads else "Unknown",
        "posts": posts_data,
        "agents": agents_data,
        "exported_at": time.time()
    }
    
    return export_data

@app.post("/import/json")
async def import_json(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1. Stop simulation
    simulation.stop_simulation()
    
    # 2. Parse JSON
    content = await file.read()
    data = json.loads(content)
    
    # 3. Clear DB
    db.query(Post).delete()
    db.query(Thread).delete()
    db.commit()
    
    # 4. Clear Agents
    agents_dir = "agents/active"
    if os.path.exists(agents_dir):
        shutil.rmtree(agents_dir)
    os.makedirs(agents_dir)

    # 5. Restore Agents
    if "agents" in data:
        for name, agent_data in data["agents"].items():
            path = os.path.join(agents_dir, name)
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "AGENT.md"), "w") as f:
                f.write(agent_data.get("agent_md", ""))
            with open(os.path.join(path, "MEMORY.md"), "w") as f:
                f.write(agent_data.get("memory_md", ""))
            with open(os.path.join(path, "TEMP.md"), "w") as f:
                f.write("")

    # 6. Restore DB
    # Create thread
    topic = data.get("topic", "Imported Discussion")
    thread = Thread(title=topic)
    db.add(thread)
    db.commit()
    
    for p_data in data.get("posts", []):
        post = Post(
            id=p_data["id"],
            thread_id=thread.id,
            agent_name=p_data["agent"],
            content=p_data["content"],
            likes=p_data["likes"],
            created_at=datetime.fromisoformat(p_data["created_at"]),
            parent_id=p_data["parent_id"]
        )
        db.merge(post)
    
    db.commit()

    return HTMLResponse('<script>window.location.reload()</script>')

@app.get("/export/html", response_class=HTMLResponse)
async def export_html_static(request: Request, db: Session = Depends(get_db)):
    posts = db.query(Post).order_by(Post.created_at.asc()).all()
    
    # Build Tree Structure
    post_map = {p.id: {"post": p, "children": []} for p in posts}
    root_nodes = []

    for p in posts:
        node = post_map[p.id]
        if p.parent_id and p.parent_id in post_map:
            post_map[p.parent_id]["children"].append(node)
        else:
            root_nodes.append(node)

    # Collect agents
    agents_dir = "agents/active"
    agents = []
    if os.path.exists(agents_dir):
         for name in os.listdir(agents_dir):
             path = os.path.join(agents_dir, name)
             if os.path.isdir(path):
                # Read files safely
                agent_md = ""
                memory_md = ""
                try:
                    with open(os.path.join(path, "AGENT.md")) as f: agent_md = f.read()
                    with open(os.path.join(path, "MEMORY.md")) as f: memory_md = f.read()
                except: pass
                
                agents.append({
                    "name": name,
                    "status": "Active",
                    "model": "Gemini 3 Pro", # Placeholder
                    "system_prompt": agent_md,
                    "agent_md": agent_md,
                    "memory_md": memory_md
                })

    return templates.TemplateResponse("export_static.html", {
        "request": request,
        "nodes": root_nodes,
        "agents": agents,
        "topic": posts[0].thread.title if posts and posts[0].thread else "Discussion"
    })

@app.get("/posts", response_class=HTMLResponse)
async def get_posts(request: Request, db: Session = Depends(get_db)):
    # 1. Fetch all posts ordered by creation time
    # (We need all to reconstruct the tree safely)
    posts = db.query(Post).order_by(Post.created_at.asc()).all()
    
    # 2. Build Tree Structure
    post_map = {p.id: {"post": p, "children": []} for p in posts}
    root_nodes = []

    for p in posts:
        node = post_map[p.id]
        if p.parent_id and p.parent_id in post_map:
            post_map[p.parent_id]["children"].append(node)
        else:
            root_nodes.append(node)
            
    # If the list is extremely long, we might want to only show the last N root nodes?
    # But for threading to work, we usually want stability.
    # Let's start with showing all. It will just scroll.
    
    return templates.TemplateResponse("thread.html", {"request": request, "nodes": root_nodes})

@app.get("/posts/json")
async def get_posts_json(db: Session = Depends(get_db)):
    """Return all posts as JSON for incremental updates."""
    posts = db.query(Post).order_by(Post.created_at.asc()).all()
    return [
        {
            "id": p.id,
            "parent_id": p.parent_id,
            "agent_name": p.agent_name,
            "content": p.content,
            "likes": p.likes,
            "created_at": p.created_at.strftime('%H:%M:%S')
        }
        for p in posts
    ]

@app.get("/agents_list", response_class=HTMLResponse)
async def get_agents_list(request: Request):
    agents_dir = "agents/active"
    agents = []
    if os.path.exists(agents_dir):
        agents = [d for d in os.listdir(agents_dir) if os.path.isdir(os.path.join(agents_dir, d))]
    
    html = '<div class="space-y-2 px-2">'
    for a in agents:
        html += f'''
        <div class="group p-3 bg-white border border-gray-100 rounded-lg cursor-pointer hover:bg-indigo-50 hover:border-indigo-100 transition-all duration-200 flex items-center gap-3 shadow-sm" hx-get="/agent/{a}" hx-target="#agent-modal-content" hx-trigger="click">
            <div class="h-8 w-8 rounded-full bg-indigo-100 text-indigo-600 flex items-center justify-center text-xs font-bold group-hover:bg-indigo-200">{a[:2].upper()}</div>
            <span class="text-sm font-medium text-gray-700 group-hover:text-indigo-700">{a}</span>
        </div>
        '''
    html += '</div>'
    return HTMLResponse(content=html)

@app.get("/agent/{name}", response_class=HTMLResponse)
async def get_agent_details(request: Request, name: str):
    # Read files
    try:
        path = os.path.join("agents", "active", name)
        with open(os.path.join(path, "AGENT.md"), "r") as f:
            agent_md = f.read()
        with open(os.path.join(path, "MEMORY.md"), "r") as f:
            memory_md = f.read()
    except:
        return HTMLResponse('<div class="p-4">Error loading agent</div>')

    return templates.TemplateResponse("agent_view.html", {
        "request": request, 
        "name": name, 
        "agent_md": agent_md, 
        "memory_md": memory_md
    })

import os
import random
import logging
import shutil
import json
from sqlalchemy.orm import Session
from database import Thread, Post, SessionLocal
from config import settings
from llm_client import llm_client

logger = logging.getLogger(__name__)

# --- Prompts ---
GENESIS_PROMPT = """You are the Simulation Controller (MOTHER). The user has provided the topic: **{TOPIC}**.
Create **{N}** distinct agents to discuss this.
**Constraint:** Ensure maximum diversity. Create a mix of optimists, pessimists, trolls, mediators, and experts.
Output a JSON list where each object contains:
`name`: string
`filename`: string (snake_case)
`agent_md_content`: string (The full text for AGENT.md)"""

DECISION_PROMPT = """**Context:**
You are {AGENT_NAME}.
Your Profile:
{AGENT_MD}

Your Memory:
{MEMORY_MD}

Forum Feed (Recent read and new posts):
{TEMP_MD}

**Task:**
Decide your next move.
1. **DO_NOTHING**: If the conversation is boring or you have nothing to add.
2. **POST**: Write a reply or a new thread. Use Markdown. You can quote others using `>`.
3. **LEAVE**: Leave the forum permanently if you are frustrated, satisfied, or bored.

**Output format (JSON only):**
{{
"action": "DO_NOTHING" | "POST" | "LEAVE",
"target_post_id": (int or null, ID of the post you are replying to),
"like_post_id": (int or null, ID of a post you want to like),
"content": (string, markdown formatted, null if action is not POST),
"updated_memory": (string, the new content for your MEMORY.md file)
}}"""

class Agent:
    def __init__(self, name_dir):
        self.name_dir = name_dir # Path to agent dir
        self.name = os.path.basename(name_dir)
        self.agent_md_path = os.path.join(name_dir, "AGENT.md")
        self.memory_md_path = os.path.join(name_dir, "MEMORY.md")
        self.temp_md_path = os.path.join(name_dir, "TEMP.md")
        self.state_path = os.path.join(name_dir, "state.json")

    def read_file(self, path):
        if os.path.exists(path):
            with open(path, "r", encoding='utf-8') as f:
                return f.read()
        return ""

    def write_file(self, path, content):
        with open(path, "w", encoding='utf-8') as f:
            f.write(content)
            
    def get_state(self):
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_state(self, state):
        with open(self.state_path, "w", encoding='utf-8') as f:
            json.dump(state, f, indent=2)

    def perceive(self, db: Session, lookback=10):
        state = self.get_state()
        last_read_id = state.get("last_read_id", 0)
        
        # 1. Recently Read (History) - Posts <= last_read_id
        read_posts_query = db.query(Post).filter(Post.id <= last_read_id).order_by(Post.created_at.desc()).limit(lookback).all()
        read_posts = read_posts_query[::-1] # Reverse to chronological
        
        # 2. Recently Added (New) - Posts > last_read_id
        new_posts = db.query(Post).filter(Post.id > last_read_id).order_by(Post.created_at.asc()).limit(20).all()
        
        content = ""
        
        content += "## Recent Read Posts\n\n"
        if not read_posts:
            content += "(None)\n\n"
        for p in read_posts:
            content += f"**ID:** {p.id} | **Author:** {p.agent_name} | **Likes:** {p.likes}\n"
            content += f"{p.content}\n\n---\n"
            
        content += "\n## Recently Added Posts (New)\n\n"
        if not new_posts:
            content += "(No new posts)\n\n"
        for p in new_posts:
            content += f"**ID:** {p.id} | **Author:** {p.agent_name} | **Likes:** {p.likes}\n"
            content += f"{p.content}\n\n---\n"
            
        self.write_file(self.temp_md_path, content)
        
        # Update state
        if new_posts:
            max_id = max(p.id for p in new_posts)
            state["last_read_id"] = max_id
            self.save_state(state)

    def decide(self):
        agent_md = self.read_file(self.agent_md_path)
        memory_md = self.read_file(self.memory_md_path)
        temp_md = self.read_file(self.temp_md_path)
        
        prompt = DECISION_PROMPT.format(
            AGENT_NAME=self.name,
            AGENT_MD=agent_md,
            MEMORY_MD=memory_md,
            TEMP_MD=temp_md
        )
        
        # Call LLM
        messages = [{"role": "user", "content": prompt}]
        response = llm_client.get_json_response(messages)
        return response

class Mother:
    def spawn_agents(self, topic, n=settings.DEFAULT_AGENT_COUNT, retries=3):
        logger.info(f"Spawning {n} agents for topic: {topic}")
        prompt = GENESIS_PROMPT.format(TOPIC=topic, N=n)
        messages = [{"role": "user", "content": prompt}]
        
        for i in range(retries):
            response = llm_client.get_json_response(messages)
            
            if response:
                agents_spawned = False
                # Expecting a list of objects
                if isinstance(response, list) and len(response) > 0:
                    for agent_data in response:
                        self.create_agent_files(agent_data)
                    agents_spawned = True
                elif isinstance(response, dict) and "agents" in response:
                     # handle case where LLM wraps list in dict
                     for agent_data in response["agents"]:
                        self.create_agent_files(agent_data)
                     agents_spawned = True
                
                if agents_spawned:
                    return

            logger.warning(f"Mother failed to spawn agents (Attempt {i+1}/{retries})")
            
        logger.error("Mother failed to spawn agents after all retries.")
        self.announce_error(topic)

    def announce_error(self, topic):
        db = SessionLocal()
        try:
            thread = db.query(Thread).first()
            if thread:
                post = Post(
                    thread_id=thread.id,
                    agent_name="System",
                    content=f"**Error:** Could not spawn agents for topic: '{topic}'. The LLM response was invalid or empty after multiple retries. Please Reset and try again.",
                    likes=0
                )
                db.add(post)
                db.commit()
        finally:
            db.close()
                
    def create_agent_files(self, agent_data):
        name = agent_data.get("name")
        filename = agent_data.get("filename", name.replace(" ", "_"))
        agent_md = agent_data.get("agent_md_content", "")
        
        # Create directory
        path = os.path.join("agents", "active", filename)
        os.makedirs(path, exist_ok=True)
        
        with open(os.path.join(path, "AGENT.md"), "w", encoding='utf-8') as f:
            f.write(agent_md)
        with open(os.path.join(path, "MEMORY.md"), "w", encoding='utf-8') as f:
            f.write("# Memory\nInitialized.")
        with open(os.path.join(path, "TEMP.md"), "w", encoding='utf-8') as f:
            f.write("")

class Simulation:
    def __init__(self):
        self.mother = Mother()
        self.running = False
        self.topic = "General AI Discussion"

    def start_simulation(self, topic):
        self.topic = topic
        self.running = True
        logger.info(f"Starting simulation on topic: {topic}")
        
        # Initialize DB Thread if needed
        db = SessionLocal()
        thread = db.query(Thread).first()
        if not thread:
            thread = Thread(title=topic)
            db.add(thread)
            db.commit()
        db.close()

        # Check existing agents
        agents_dir = os.path.join("agents", "active")
        if not os.path.exists(agents_dir):
             os.makedirs(agents_dir)
             
        if not os.listdir(agents_dir):
             self.mother.spawn_agents(topic)

    def stop_simulation(self):
        self.running = False

    def step(self):
        if not self.running:
            return

        db = SessionLocal()
        try:
            # 1. Select random agent
            active_agents_dir = os.path.join("agents", "active")
            agent_names = [d for d in os.listdir(active_agents_dir) if os.path.isdir(os.path.join(active_agents_dir, d))]
            
            if not agent_names:
                logger.info("No active agents.")
                return
                
            agent_name = random.choice(agent_names)
            agent = Agent(os.path.join(active_agents_dir, agent_name))
            
            logger.info(f"Agent {agent.name} is thinking...")
            
            # 2. Perceive
            agent.perceive(db)
            
            # 3. Decide
            decision = agent.decide()
            if not decision:
                logger.warning(f"Agent {agent.name} failed to decide (JSON parse error or API fail).")
                return
            
            action = decision.get("action")
            logger.info(f"Agent {agent.name} decided to {action}")
            
            # 4. Execute
            if action == "POST":
                # Ensure thread exists
                thread = db.query(Thread).first()
                if not thread:
                    thread = Thread(title=self.topic)
                    db.add(thread)
                    db.commit()
                
                content = decision.get("content")
                if content:
                    target_id = decision.get("target_post_id") 
                    # If target_id is invalid/not found, we can treat as root post or just ignore parent
                    if target_id and not db.query(Post).filter(Post.id == target_id).first():
                        target_id = None

                    post = Post(
                        thread_id=thread.id,
                        agent_name=agent.name,
                        content=content,
                        parent_id=target_id
                    )
                    db.add(post)
                    db.commit()
            
            elif action == "LEAVE":
                # Move to inactive
                src = os.path.join("agents", "active", agent.name)
                dst = os.path.join("agents", "inactive", agent.name)
                # Ensure inactive dir for this agent doesn't exist or rename
                if os.path.exists(dst):
                     dst = dst + f"_{int(time.time())}"
                
                if os.path.exists(src):
                    shutil.move(src, dst)
                    logger.info(f"Agent {agent.name} left the forum.")
                    # Write postmortem if needed
            
            # Like?
            like_id = decision.get("like_post_id")
            if like_id:
                post = db.query(Post).filter(Post.id == like_id).first()
                if post:
                    post.likes += 1
                    db.commit()
            
            # Update Memory
            new_memory = decision.get("updated_memory")
            if new_memory:
                agent.write_file(agent.memory_md_path, new_memory)

        except Exception as e:
            logger.exception(f"Error in simulation step: {e}")
        finally:
            db.close()

# Singleton instance
simulation = Simulation()

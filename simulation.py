import os
import random
import logging
import shutil
import json
import re
import time
from datetime import datetime
from sqlalchemy.orm import Session
from database import Thread, Post, SessionLocal
from config import settings
from llm_client import llm_client
from web_browser import web_browser

logger = logging.getLogger(__name__)

# --- Language Detection ---
def detect_language(text):
    """Simple language detection using character ranges and common patterns."""
    text = text.strip()
    if not text:
        return "English"
    
    # Count characters in different scripts
    korean = len(re.findall(r'[\uAC00-\uD7AF\u1100-\u11FF]', text))
    japanese = len(re.findall(r'[\u3040-\u309F\u30A0-\u30FF]', text))
    chinese = len(re.findall(r'[\u4E00-\u9FFF]', text))
    cyrillic = len(re.findall(r'[\u0400-\u04FF]', text))
    arabic = len(re.findall(r'[\u0600-\u06FF]', text))
    thai = len(re.findall(r'[\u0E00-\u0E7F]', text))
    hebrew = len(re.findall(r'[\u0590-\u05FF]', text))
    devanagari = len(re.findall(r'[\u0900-\u097F]', text))
    
    # Spanish/French/German detection via accents and common words
    latin_accented = len(re.findall(r'[àáâãäåçèéêëìíîïñòóôõöùúûüýÿœæ]', text.lower()))
    
    total_chars = len(text)
    if total_chars == 0:
        return "English"
    
    # Check for dominant script
    if korean / total_chars > 0.1:
        return "Korean"
    if japanese / total_chars > 0.1:
        return "Japanese"
    if chinese / total_chars > 0.1:
        return "Chinese"
    if cyrillic / total_chars > 0.1:
        return "Russian"
    if arabic / total_chars > 0.1:
        return "Arabic"
    if thai / total_chars > 0.1:
        return "Thai"
    if hebrew / total_chars > 0.1:
        return "Hebrew"
    if devanagari / total_chars > 0.1:
        return "Hindi"
    
    # For Latin scripts, use common word patterns
    text_lower = text.lower()
    
    # Spanish indicators
    spanish_words = ['que', 'de', 'el', 'la', 'los', 'las', 'es', 'en', 'por', 'para', 'con', 'como', 'más', 'pero', 'sobre', '¿', '¡']
    if any(w in text_lower.split() for w in spanish_words) or '¿' in text or '¡' in text:
        return "Spanish"
    
    # French indicators
    french_words = ['le', 'la', 'les', 'de', 'du', 'des', 'est', 'sont', 'avec', 'pour', 'dans', 'sur', "qu'", "c'est", 'très', 'être']
    if any(w in text_lower.split() for w in french_words) or "'" in text and latin_accented > 0:
        if any(w in text_lower for w in ['qu\'', 'c\'est', 'n\'est', 'd\'un']):
            return "French"
    
    # German indicators
    german_words = ['der', 'die', 'das', 'und', 'ist', 'nicht', 'von', 'mit', 'für', 'auf', 'sind', 'werden', 'auch', 'über']
    if any(w in text_lower.split() for w in german_words) or 'ß' in text or 'ü' in text or 'ö' in text or 'ä' in text:
        return "German"
    
    # Portuguese indicators  
    portuguese_words = ['que', 'de', 'não', 'em', 'para', 'com', 'uma', 'são', 'também', 'mais', 'muito', 'pela', 'pelo']
    if any(w in text_lower.split() for w in portuguese_words) or 'ã' in text or 'ç' in text:
        return "Portuguese"
    
    # Italian indicators
    italian_words = ['che', 'di', 'non', 'è', 'per', 'sono', 'con', 'della', 'anche', 'come', 'questo', 'quello', 'tutto']
    if any(w in text_lower.split() for w in italian_words):
        return "Italian"
    
    # Dutch indicators
    dutch_words = ['de', 'het', 'een', 'van', 'en', 'is', 'niet', 'dat', 'op', 'zijn', 'voor', 'met', 'aan', 'naar']
    if any(w in text_lower.split() for w in dutch_words) and 'ij' in text_lower:
        return "Dutch"
    
    return "English"

# --- Prompts ---
GENESIS_PROMPT = """You are the Simulation Controller (MOTHER). The user has provided the topic: **{TOPIC}**.

**Current Time:** {CURRENT_TIME} (format: YY/MM/DD HH:MM:SS)

**Language:** The topic is in **{LANGUAGE}**. All agents MUST communicate in {LANGUAGE} throughout the discussion. Their personas, writing style, and all forum posts should be in {LANGUAGE}.

Create **{N}** distinct agents to discuss this topic.
**Constraint:** Ensure maximum diversity. Create a mix of optimists, pessimists, trolls, mediators, and experts. All agents must write in {LANGUAGE}.

Output a JSON list where each object contains:
`name`: string (can be a {LANGUAGE} name if appropriate)
`filename`: string (snake_case, ASCII only)
`agent_md_content`: string (The full text for AGENT.md, written in {LANGUAGE})"""

DECISION_PROMPT = """**Context:**
You are {AGENT_NAME}.

**Current Time:** {CURRENT_TIME} (format: YY/MM/DD HH:MM:SS)

**Language:** You MUST write all content in **{LANGUAGE}**. Do not switch languages.

Your Profile:
{AGENT_MD}

Your Memory:
{MEMORY_MD}

Forum Feed (Recent read and new posts):
{TEMP_MD}

**Task:**
Decide your next move. Remember to write in {LANGUAGE}.
1. **DO_NOTHING**: If the conversation is boring or you have nothing to add.
2. **POST**: Write a reply or a new thread. Use Markdown. You can quote others using `>`. Write in {LANGUAGE}.
3. **LEAVE**: Leave the forum permanently if you are frustrated, satisfied, or bored.
{WEB_OPTIONS}
**Important:** When making factual claims or discussing complex topics, consider using SEARCH or BROWSE to find and cite reliable sources. This adds credibility to your posts.

**Output format (JSON only):**
{{
"action": "DO_NOTHING" | "POST" | "LEAVE"{WEB_ACTIONS},
"target_post_id": (int or null, ID of the post you are replying to),
"like_post_id": (int or null, ID of a post you want to like),
"content": (string in {LANGUAGE}, markdown formatted, null if action is not POST),{WEB_FIELDS}
"updated_memory": (string in {LANGUAGE}, the new content for your MEMORY.md file)
}}"""

WEB_OPTIONS_TEXT = """4. **SEARCH**: Search the web for information.
   - Use this to find facts, statistics, sources, or verify claims.
   - Recommended when discussing scientific topics, current events, or making factual claims.
   - You'll receive search results with URLs that you can then BROWSE.
5. **BROWSE**: Look up a specific web page.
   - Use this when you have a URL (e.g., from search results) or know a reliable source.
   - Allowed sources: {ALLOWED_SOURCES}.
   - Great for citing Wikipedia, research papers, or news articles to support your arguments.

**Note:** You can only perform ONE web action (SEARCH or BROWSE) per turn. After receiving results, you can POST or take another action.
**Pro tip:** Using SEARCH or BROWSE to cite sources makes your posts more credible and interesting!
"""

WEB_FIELDS_TEXT = """
"search_query": (string or null, required if action is SEARCH),
"browse_url": (string or null, required if action is BROWSE - must be from allowed sources),
"browse_reason": (string or null, why you want to look this up),"""

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
            post_time = p.created_at.strftime("%y/%m/%d %H:%M:%S") if p.created_at else "Unknown"
            content += f"**ID:** {p.id} | **Time:** {post_time} | **Author:** {p.agent_name} | **Likes:** {p.likes}\n"
            content += f"{p.content}\n\n---\n"
            
        content += "\n## Recently Added Posts (New)\n\n"
        if not new_posts:
            content += "(No new posts)\n\n"
        for p in new_posts:
            post_time = p.created_at.strftime("%y/%m/%d %H:%M:%S") if p.created_at else "Unknown"
            content += f"**ID:** {p.id} | **Time:** {post_time} | **Author:** {p.agent_name} | **Likes:** {p.likes}\n"
            content += f"{p.content}\n\n---\n"
            
        self.write_file(self.temp_md_path, content)
        
        # Update state
        if new_posts:
            max_id = max(p.id for p in new_posts)
            state["last_read_id"] = max_id
            self.save_state(state)

    def decide(self, language="English", allow_web=True):
        agent_md = self.read_file(self.agent_md_path)
        memory_md = self.read_file(self.memory_md_path)
        temp_md = self.read_file(self.temp_md_path)
        
        # Build web-related prompt parts (SEARCH and BROWSE)
        if allow_web and settings.ENABLE_WEB_BROWSE:
            from web_browser import web_browser
            allowed_sources = web_browser.get_allowed_domains_description()
            web_options = WEB_OPTIONS_TEXT.format(ALLOWED_SOURCES=allowed_sources)
            web_actions = ' | "SEARCH" | "BROWSE"'
            web_fields = WEB_FIELDS_TEXT
        else:
            web_options = ""
            web_actions = ""
            web_fields = ""
        
        current_time = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        prompt = DECISION_PROMPT.format(
            AGENT_NAME=self.name,
            AGENT_MD=agent_md,
            MEMORY_MD=memory_md,
            TEMP_MD=temp_md,
            LANGUAGE=language,
            WEB_OPTIONS=web_options,
            WEB_ACTIONS=web_actions,
            WEB_FIELDS=web_fields,
            CURRENT_TIME=current_time,
        )
        
        # Call LLM
        messages = [{"role": "user", "content": prompt}]
        response = llm_client.get_json_response(messages)
        return response

class Mother:
    def spawn_agents(self, topic, n=settings.DEFAULT_AGENT_COUNT, retries=3, language="English"):
        logger.info(f"Spawning {n} agents for topic: {topic} (Language: {language})")
        current_time = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        prompt = GENESIS_PROMPT.format(TOPIC=topic, N=n, LANGUAGE=language, CURRENT_TIME=current_time)
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
        self.language = "English"

    def start_simulation(self, topic):
        self.topic = topic
        self.language = detect_language(topic)
        self.running = True
        logger.info(f"Starting simulation on topic: {topic} (Detected language: {self.language})")
        
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
             self.mother.spawn_agents(topic, language=self.language)

    def stop_simulation(self):
        self.running = False

    def _handle_search(self, agent, decision):
        """
        Handle SEARCH action: perform web search and get follow-up decision.
        
        Returns: (new_decision, new_action) or (None, None) if failed
        """
        query = decision.get("search_query", "")
        logger.info(f"Agent {agent.name} searching: {query}")
        
        result = web_browser.search(query)
        
        if result["success"]:
            search_context = web_browser.format_search_results(query, result["results"])
            logger.info(f"Agent {agent.name} found {len(result['results'])} results")
        else:
            search_context = f"\n\n## Search Results for: \"{query}\"\n**Error:** {result['error']}\n"
            logger.warning(f"Agent {agent.name} search failed: {result['error']}")
        
        # Add explicit notice that search was completed
        search_context += "\n\n---\n**[SYSTEM] You have completed your SEARCH action for this turn. You may now BROWSE one of the URLs above, POST your response, or take another action. You cannot SEARCH again this turn.**\n---\n"
        
        # Append search results to TEMP.md
        current_temp = agent.read_file(agent.temp_md_path)
        agent.write_file(agent.temp_md_path, current_temp + search_context)
        
        # Second decision (with browse still available so they can follow up)
        new_decision = agent.decide(language=self.language, allow_web=True)
        if not new_decision:
            logger.warning(f"Agent {agent.name} failed post-search decision.")
            return None, None
        
        new_action = new_decision.get("action")
        logger.info(f"Agent {agent.name} post-search decided to {new_action}")
        
        # If they want to search again, just treat as DO_NOTHING to prevent loops
        if new_action == "SEARCH":
            logger.info(f"Agent {agent.name} wanted to search again, skipping.")
            new_action = "DO_NOTHING"
        
        return new_decision, new_action

    def _handle_browse(self, agent, decision):
        """
        Handle BROWSE action: fetch and summarize web page, get follow-up decision.
        
        Returns: (new_decision, new_action) or (None, None) if failed
        """
        url = decision.get("browse_url")
        reason = decision.get("browse_reason", "general research")
        
        logger.info(f"Agent {agent.name} browsing: {url}")
        
        # Fetch the page
        result = web_browser.fetch(url)
        
        if result["success"]:
            # Summarize the content
            summary = web_browser.summarize(
                result["content"], url, reason, llm_client
            )
            title = result.get("title", "")
            browse_context = f"\n\n## Web Browse Result\n**URL:** {url}\n**Title:** {title}\n**Summary:**\n{summary}\n"
            logger.info(f"Agent {agent.name} successfully browsed {url}")
        else:
            browse_context = f"\n\n## Web Browse Result\n**URL:** {url}\n**Error:** {result['error']}\n"
            logger.warning(f"Agent {agent.name} failed to browse {url}: {result['error']}")
        
        # Add explicit notice that browse was completed
        browse_context += "\n\n---\n**[SYSTEM] You have completed your web action for this turn. You should now POST your response using the information above, or take another non-web action. You cannot SEARCH or BROWSE again this turn.**\n---\n"
        
        # Append browse result to TEMP.md
        current_temp = agent.read_file(agent.temp_md_path)
        agent.write_file(agent.temp_md_path, current_temp + browse_context)
        
        # Second decision (without web options to prevent infinite loops)
        new_decision = agent.decide(language=self.language, allow_web=False)
        if not new_decision:
            logger.warning(f"Agent {agent.name} failed post-browse decision.")
            return None, None
        
        new_action = new_decision.get("action")
        logger.info(f"Agent {agent.name} post-browse decided to {new_action}")
        
        return new_decision, new_action

    def _handle_post(self, agent, decision, db):
        """Handle POST action: create a new post in the forum."""
        # Ensure thread exists
        thread = db.query(Thread).first()
        if not thread:
            thread = Thread(title=self.topic)
            db.add(thread)
            db.commit()
        
        content = decision.get("content")
        if content:
            target_id = decision.get("target_post_id") 
            # If target_id is invalid/not found, treat as root post
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

    def _handle_leave(self, agent):
        """Handle LEAVE action: move agent to inactive directory."""
        src = os.path.join("agents", "active", agent.name)
        dst = os.path.join("agents", "inactive", agent.name)
        # Ensure inactive dir for this agent doesn't exist or rename
        if os.path.exists(dst):
            dst = dst + f"_{int(time.time())}"
        
        if os.path.exists(src):
            shutil.move(src, dst)
            logger.info(f"Agent {agent.name} left the forum.")

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
            
            # 3. Decide (with web options if enabled)
            decision = agent.decide(language=self.language, allow_web=True)
            if not decision:
                logger.warning(f"Agent {agent.name} failed to decide (JSON parse error or API fail).")
                return
            
            action = decision.get("action")
            logger.info(f"Agent {agent.name} decided to {action}")
            
            # 4. Handle web actions (SEARCH, BROWSE)
            if action == "SEARCH" and settings.ENABLE_WEB_BROWSE:
                decision, action = self._handle_search(agent, decision)
                if decision is None:
                    return
            
            if action == "BROWSE" and settings.ENABLE_WEB_BROWSE:
                decision, action = self._handle_browse(agent, decision)
                if decision is None:
                    return
            
            if action in ("BROWSE", "SEARCH") and not settings.ENABLE_WEB_BROWSE:
                logger.warning(f"Agent {agent.name} tried to {action} but web browsing is disabled.")
                action = "DO_NOTHING"
            
            # 5. Execute final action
            if action == "POST":
                self._handle_post(agent, decision, db)
            elif action == "LEAVE":
                self._handle_leave(agent)
            
            # 6. Handle likes
            like_id = decision.get("like_post_id")
            if like_id:
                post = db.query(Post).filter(Post.id == like_id).first()
                if post:
                    post.likes += 1
                    db.commit()
            
            # 7. Update Memory
            new_memory = decision.get("updated_memory")
            if new_memory:
                agent.write_file(agent.memory_md_path, new_memory)

        except Exception as e:
            logger.exception(f"Error in simulation step: {e}")
        finally:
            db.close()

# Singleton instance
simulation = Simulation()

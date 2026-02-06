"""
Multi-user Simulation Manager for LocalBBS.
Handles running multiple simulations concurrently for different users.
"""

import threading
import time
import logging
import json
import re
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from database import SessionLocal, Simulation as SimulationModel, Thread, Post, Agent as AgentModel
from llm_client import llm_client
from web_browser import web_browser
from simulation import detect_language, GENESIS_PROMPT, POOL_STYLE_DESCRIPTIONS, DECISION_PROMPT, WEB_OPTIONS_TEXT, WEB_FIELDS_TEXT, MOTHER_INTERVENTION_PROMPT

logger = logging.getLogger(__name__)


class SimulationRunner:
    """Runs a single simulation instance."""
    
    def __init__(self, simulation_id: int):
        self.simulation_id = simulation_id
        
    def _get_db(self) -> Session:
        """Get a new database session."""
        return SessionLocal()
    
    def _get_simulation(self, db: Session) -> Optional[SimulationModel]:
        """Get the simulation model with user relationship loaded."""
        return db.query(SimulationModel).filter(SimulationModel.id == self.simulation_id).first()
    
    def _get_api_key(self, db: Session, sim: SimulationModel) -> Optional[str]:
        """Get the API key for this simulation's user."""
        from config import settings
        # Try user's personal key first, fall back to global key
        if sim.user and sim.user.openrouter_api_key:
            return sim.user.openrouter_api_key
        return settings.OPENROUTER_API_KEY or None
    
    def step(self) -> bool:
        """Execute one simulation step. Returns False if simulation should stop."""
        db = self._get_db()
        try:
            sim = self._get_simulation(db)
            if not sim:
                logger.warning(f"Simulation {self.simulation_id} not found")
                return False
            
            if sim.status != "running":
                return False
            
            # Check loop limit
            if sim.max_loops and sim.loop_count >= sim.max_loops:
                logger.info(f"Simulation {self.simulation_id} reached max loops ({sim.max_loops})")
                sim.status = "stopped"
                db.commit()
                return False
            
            # Get or create thread
            thread = db.query(Thread).filter(Thread.simulation_id == sim.id).first()
            if not thread:
                thread = Thread(simulation_id=sim.id, title=sim.topic)
                db.add(thread)
                db.commit()
                db.refresh(thread)
            
            # Get agents
            agents = db.query(AgentModel).filter(
                AgentModel.simulation_id == sim.id,
                AgentModel.status == "active"
            ).all()
            
            # Spawn initial agents if none exist
            if not agents:
                logger.info(f"Simulation {self.simulation_id}: Spawning initial agents")
                api_key = self._get_api_key(db, sim)
                self._spawn_initial_agents(db, sim, api_key)
                agents = db.query(AgentModel).filter(
                    AgentModel.simulation_id == sim.id,
                    AgentModel.status == "active"
                ).all()
            
            if not agents:
                logger.warning(f"Simulation {self.simulation_id}: No agents available")
                return True  # Continue trying
            
            # Select an agent to act
            import random
            agent = random.choice(agents)
            
            self._update_agent_context(db, agent, thread, sim)
            
            # Get API key for LLM calls
            api_key = self._get_api_key(db, sim)
            
            # Get agent decision
            decision = self._get_agent_decision(agent, sim, api_key)
            
            if decision:
                action = decision.get("action", "DO_NOTHING")
                
                if action == "POST":
                    self._handle_post(db, agent, decision, thread, sim)
                    sim.consecutive_idle_count = 0
                elif action == "LEAVE":
                    agent.status = "left"
                    sim.consecutive_idle_count = 0
                    logger.info(f"Agent {agent.name} left simulation {sim.id}")
                elif action == "SEARCH":
                    self._handle_search(db, agent, decision, thread, sim, api_key)
                    sim.consecutive_idle_count = 0
                elif action == "BROWSE":
                    self._handle_browse(db, agent, decision, thread, sim, api_key)
                    sim.consecutive_idle_count = 0
                else:
                    sim.consecutive_idle_count += 1
                
                # Update memory if provided
                if decision.get("updated_memory"):
                    agent.memory_md = decision["updated_memory"]
                
                # Handle likes
                if decision.get("like_post_id"):
                    post = db.query(Post).filter(Post.id == decision["like_post_id"]).first()
                    if post:
                        post.likes += 1
            else:
                sim.consecutive_idle_count += 1
            
            # Check for mother intervention
            mother_threshold = 5  # Default threshold
            if sim.consecutive_idle_count >= mother_threshold:
                logger.info(f"Simulation {self.simulation_id}: Mother intervention triggered")
                self._mother_intervention(db, sim, thread, api_key)
                sim.consecutive_idle_count = 0
            
            # Increment loop count
            sim.loop_count += 1
            sim.updated_at = datetime.utcnow()
            db.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"Simulation {self.simulation_id} error: {e}", exc_info=True)
            db.rollback()
            return True  # Continue despite error
        finally:
            db.close()
    
    def _spawn_initial_agents(self, db: Session, sim: SimulationModel, api_key: str = None):
        """Spawn initial agents for a new simulation."""
        pool_style = sim.pool_style or "professional"
        pool_style_desc = POOL_STYLE_DESCRIPTIONS.get(pool_style, POOL_STYLE_DESCRIPTIONS["professional"])
        current_time = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        
        prompt = GENESIS_PROMPT.format(
            TOPIC=sim.topic,
            CURRENT_TIME=current_time,
            LANGUAGE=sim.language,
            POOL_STYLE=pool_style,
            POOL_STYLE_DESCRIPTION=pool_style_desc,
            N=sim.agent_count or 3
        )
        
        try:
            messages = [{"role": "user", "content": prompt}]
            response = llm_client.chat_completion(messages, api_key=api_key, model=sim.model_name)
            
            if not response:
                logger.error(f"No response from LLM for genesis")
                return
            
            # Parse JSON from response
            json_match = re.search(r'\[[\s\S]*\]', response)
            if not json_match:
                logger.error(f"Failed to parse genesis response: {response[:500]}")
                return
            
            agents_data = json.loads(json_match.group())
            
            for agent_data in agents_data:
                agent = AgentModel(
                    simulation_id=sim.id,
                    name=agent_data.get("name", "Unknown"),
                    directory_name=agent_data.get("filename", "unknown"),
                    agent_md=agent_data.get("agent_md_content", ""),
                    memory_md="",
                    temp_md="",
                    status="active"
                )
                db.add(agent)
            
            db.commit()
            logger.info(f"Spawned {len(agents_data)} agents for simulation {sim.id}")
            
        except Exception as e:
            logger.error(f"Failed to spawn agents: {e}", exc_info=True)
    
    def _update_agent_context(self, db: Session, agent: AgentModel, thread: Thread, sim: SimulationModel):
        """Update agent's TEMP.md with recent posts."""
        posts = db.query(Post).filter(
            Post.thread_id == thread.id,
            Post.id > agent.last_read_post_id
        ).order_by(Post.created_at.asc()).all()
        
        if not posts:
            agent.temp_md = "No new posts since your last check."
            return
        
        temp_content = f"## New Posts (since your last check)\n\n"
        for post in posts:
            temp_content += f"### Post #{post.id} by {post.agent_name}\n"
            temp_content += f"*{post.created_at.strftime('%y/%m/%d %H:%M:%S')}*"
            if post.parent_id:
                temp_content += f" (reply to #{post.parent_id})"
            temp_content += f"\n\n{post.content}\n\n---\n\n"
        
        agent.temp_md = temp_content
        agent.last_read_post_id = posts[-1].id if posts else agent.last_read_post_id
    
    def _get_agent_decision(self, agent: AgentModel, sim: SimulationModel, api_key: str = None) -> Optional[Dict[str, Any]]:
        """Get decision from an agent."""
        current_time = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        
        web_options = ""
        web_fields = ""
        web_actions = ""
        
        if sim.enable_web_browse:
            allowed_msg = "Any URL is allowed."
            if sim.web_browse_safety_mode == "allowlist":
                allowed_msg = "Only URLs from trusted sources are allowed."
            
            web_options = WEB_OPTIONS_TEXT.format(ALLOWED_SOURCES=allowed_msg)
            web_fields = WEB_FIELDS_TEXT
            web_actions = ' | "SEARCH" | "BROWSE"'
        
        prompt = DECISION_PROMPT.format(
            AGENT_NAME=agent.name,
            CURRENT_TIME=current_time,
            LANGUAGE=sim.language,
            AGENT_MD=agent.agent_md or "No persona defined.",
            MEMORY_MD=agent.memory_md or "No memories yet.",
            TEMP_MD=agent.temp_md or "No new activity.",
            WEB_OPTIONS=web_options,
            WEB_ACTIONS=web_actions,
            WEB_FIELDS=web_fields
        )
        
        try:
            messages = [{"role": "user", "content": prompt}]
            response = llm_client.chat_completion(messages, api_key=api_key, model=sim.model_name)
            
            if not response:
                logger.warning(f"Agent {agent.name}: No response from LLM")
                return None
            
            # Parse JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                logger.warning(f"Agent {agent.name} returned non-JSON: {response[:200]}")
                return None
            
            return json.loads(json_match.group())
            
        except Exception as e:
            logger.error(f"Agent {agent.name} decision error: {e}")
            return None
    
    def _handle_post(self, db: Session, agent: AgentModel, decision: Dict, thread: Thread, sim: SimulationModel):
        """Handle a POST action from an agent."""
        content = decision.get("content", "")
        if not content:
            return
        
        parent_id = decision.get("target_post_id")
        
        post = Post(
            thread_id=thread.id,
            agent_name=agent.name,
            content=content,
            parent_id=parent_id
        )
        db.add(post)
        db.commit()
        
        logger.info(f"Agent {agent.name} posted in simulation {sim.id}")
    
    def _handle_search(self, db: Session, agent: AgentModel, decision: Dict, thread: Thread, sim: SimulationModel, api_key: str = None):
        """Handle a SEARCH action."""
        query = decision.get("search_query", "")
        if not query:
            return
        
        logger.info(f"Agent {agent.name} searching: {query}")
        result = web_browser.search(query)
        
        if result["success"]:
            search_context = web_browser.format_search_results(query, result["results"])
        else:
            search_context = f"\n\n## Search Results for: \"{query}\"\n**Error:** {result['error']}\n"
        
        # Append to agent's temp
        agent.temp_md = (agent.temp_md or "") + search_context
        
        # Get follow-up decision
        follow_up = self._get_agent_decision(agent, sim, api_key)
        if follow_up and follow_up.get("action") == "POST":
            self._handle_post(db, agent, follow_up, thread, sim)
    
    def _handle_browse(self, db: Session, agent: AgentModel, decision: Dict, thread: Thread, sim: SimulationModel, api_key: str = None):
        """Handle a BROWSE action."""
        url = decision.get("browse_url", "")
        if not url:
            return
        
        logger.info(f"Agent {agent.name} browsing: {url}")
        result = web_browser.fetch(url)
        
        if result["success"]:
            reason = decision.get("browse_reason", "research")
            summary = web_browser.summarize(result["content"], url, reason, llm_client, api_key=api_key)
            title = result.get("title", "")
            browse_context = f"\n\n## Web Browse Result\n**URL:** {url}\n**Title:** {title}\n**Summary:**\n{summary}\n"
        else:
            browse_context = f"\n\n## Web Browse Result\n**URL:** {url}\n**Error:** {result['error']}\n"
        
        # Append to agent's temp
        agent.temp_md = (agent.temp_md or "") + browse_context
        
        # Get follow-up decision
        follow_up = self._get_agent_decision(agent, sim, api_key)
        if follow_up and follow_up.get("action") == "POST":
            self._handle_post(db, agent, follow_up, thread, sim)
    
    def _mother_intervention(self, db: Session, sim: SimulationModel, thread: Thread, api_key: str = None):
        """Handle mother intervention when discussion stagnates."""
        current_time = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        
        # Get recent posts
        posts = db.query(Post).filter(Post.thread_id == thread.id).order_by(Post.created_at.desc()).limit(10).all()
        recent_posts = "\n\n".join([f"**{p.agent_name}:** {p.content[:200]}..." for p in reversed(posts)])
        
        prompt = MOTHER_INTERVENTION_PROMPT.format(
            TOPIC=sim.topic,
            CURRENT_TIME=current_time,
            LANGUAGE=sim.language,
            POOL_STYLE=sim.pool_style,
            IDLE_COUNT=sim.consecutive_idle_count,
            RECENT_POSTS=recent_posts or "No posts yet."
        )
        
        try:
            messages = [{"role": "user", "content": prompt}]
            response = llm_client.chat_completion(messages, api_key=api_key, model=sim.model_name)
            
            if not response:
                return
            
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                return
            
            decision = json.loads(json_match.group())
            action = decision.get("action")
            
            if action == "INJECT_QUESTION":
                content = decision.get("content", "")
                if content:
                    post = Post(
                        thread_id=thread.id,
                        agent_name="[MOTHER]",
                        content=content
                    )
                    db.add(post)
                    logger.info(f"Mother injected question in simulation {sim.id}")
            
            elif action == "SPAWN_AGENT":
                agent_data = decision.get("agent_data", {})
                if agent_data:
                    agent = AgentModel(
                        simulation_id=sim.id,
                        name=agent_data.get("name", "New Agent"),
                        directory_name=agent_data.get("filename", "new_agent"),
                        agent_md=agent_data.get("agent_md_content", ""),
                        memory_md="",
                        temp_md="",
                        status="active"
                    )
                    db.add(agent)
                    logger.info(f"Mother spawned new agent in simulation {sim.id}")
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Mother intervention error: {e}")


class SimulationManager:
    """Manages all running simulations."""
    
    def __init__(self):
        self.runners: Dict[int, SimulationRunner] = {}
        self.lock = threading.Lock()
        self.running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start the simulation manager background thread."""
        if self.running:
            return
        
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("SimulationManager started")
    
    def stop(self):
        """Stop the simulation manager."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("SimulationManager stopped")
    
    def _run_loop(self):
        """Main loop that runs all active simulations."""
        while self.running:
            try:
                self._step_all_simulations()
            except Exception as e:
                logger.error(f"SimulationManager error: {e}", exc_info=True)
            
            time.sleep(2)  # Global step interval
    
    def _step_all_simulations(self):
        """Execute one step for all running simulations."""
        db = SessionLocal()
        try:
            # Get all running simulations
            running_sims = db.query(SimulationModel).filter(
                SimulationModel.status == "running"
            ).all()
            
            for sim in running_sims:
                with self.lock:
                    if sim.id not in self.runners:
                        self.runners[sim.id] = SimulationRunner(sim.id)
                
                runner = self.runners[sim.id]
                
                # Execute step with individual delay
                try:
                    runner.step()
                except Exception as e:
                    logger.error(f"Runner {sim.id} step error: {e}")
                
                # Sleep for simulation's configured delay
                time.sleep(sim.loop_delay or 2.0)
            
            # Clean up runners for stopped simulations
            with self.lock:
                active_ids = {s.id for s in running_sims}
                to_remove = [sid for sid in self.runners if sid not in active_ids]
                for sid in to_remove:
                    del self.runners[sid]
                    
        finally:
            db.close()


# Global simulation manager instance
simulation_manager = SimulationManager()

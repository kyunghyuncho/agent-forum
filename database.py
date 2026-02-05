from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Text, Float, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
from config import settings

Base = declarative_base()

# ============================================================================
# User & Authentication Models
# ============================================================================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100))
    openrouter_api_key = Column(String(255))  # User's own API key
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Admin & verification
    is_admin = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    verification_token = Column(String(255), unique=True, nullable=True)
    verification_token_expires = Column(DateTime, nullable=True)
    
    # Relationships
    simulations = relationship("Simulation", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")


class UserSettings(Base):
    """Persistent user settings - saved and restored on login."""
    __tablename__ = "user_settings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # LLM Settings
    default_model = Column(String(255), default="google/gemini-2.5-flash-lite-preview-09-2025")
    
    # Simulation Defaults
    default_agent_count = Column(Integer, default=3)
    default_max_loops = Column(Integer, default=50)
    default_loop_delay = Column(Float, default=2.0)
    default_pool_style = Column(String(50), default="professional")
    mother_intervention_threshold = Column(Integer, default=5)
    mother_lookback_k = Column(Integer, default=25)
    
    # Web Browsing Settings
    enable_web_browse = Column(Boolean, default=True)
    web_browse_safety_mode = Column(String(50), default="safebrowsing")
    web_browse_timeout = Column(Integer, default=10)
    
    # UI Preferences
    theme = Column(String(20), default="light")
    posts_per_page = Column(Integer, default=50)
    auto_scroll = Column(Boolean, default=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="settings")


class GlobalSettings(Base):
    """Global application settings - managed by admins."""
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True, index=True)
    
    # API Keys (global, not per-user)
    google_safe_browsing_api_key = Column(String(255))
    resend_api_key = Column(String(255))  # For email sending
    
    # Email Settings
    email_from_address = Column(String(255), default="noreply@localbbs.app")
    email_from_name = Column(String(100), default="LocalBBS")
    require_email_verification = Column(Boolean, default=True)
    
    # Registration Settings
    allow_registration = Column(Boolean, default=True)
    
    # App Settings
    app_name = Column(String(100), default="LocalBBS")
    app_url = Column(String(255))  # Base URL for email links
    max_simulations_per_user = Column(Integer, default=5)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


# ============================================================================
# Simulation Models
# ============================================================================

class Simulation(Base):
    """A simulation instance owned by a user."""
    __tablename__ = "simulations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Simulation info
    topic = Column(String(500), nullable=False)
    language = Column(String(50), default="English")
    pool_style = Column(String(50), default="professional")
    status = Column(String(20), default="stopped")  # 'running', 'stopped', 'paused'
    loop_count = Column(Integer, default=0)
    consecutive_idle_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Per-simulation settings (copied from user defaults on creation)
    model_name = Column(String(255), default="google/gemini-2.5-flash-lite-preview-09-2025")
    max_loops = Column(Integer, default=50)
    loop_delay = Column(Float, default=2.0)
    agent_count = Column(Integer, default=3)
    enable_web_browse = Column(Boolean, default=True)
    web_browse_safety_mode = Column(String(50), default="safebrowsing")
    
    # Relationships
    user = relationship("User", back_populates="simulations")
    threads = relationship("Thread", back_populates="simulation", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="simulation", cascade="all, delete-orphan")


class Thread(Base):
    """A discussion thread within a simulation."""
    __tablename__ = "threads"
    id = Column(Integer, primary_key=True, index=True)
    simulation_id = Column(Integer, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(500), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")  # active, closed
    
    # Relationships
    simulation = relationship("Simulation", back_populates="threads")
    posts = relationship("Post", back_populates="thread", cascade="all, delete-orphan")


class Post(Base):
    """A post within a thread."""
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("threads.id", ondelete="CASCADE"))
    agent_name = Column(String(255))
    content = Column(Text)
    parent_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    likes = Column(Integer, default=0)
    
    # Relationships
    thread = relationship("Thread", back_populates="posts")


class Agent(Base):
    """An AI agent within a simulation (previously stored as files)."""
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True, index=True)
    simulation_id = Column(Integer, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String(255), nullable=False)
    directory_name = Column(String(255), nullable=False)  # snake_case identifier
    
    # Content (previously in files)
    agent_md = Column(Text)      # Persona (was AGENT.md)
    memory_md = Column(Text)     # Memory (was MEMORY.md)
    temp_md = Column(Text)       # Ephemeral context (was TEMP.md)
    
    # State (previously in state.json)
    last_read_post_id = Column(Integer, default=0)
    
    status = Column(String(20), default="active")  # 'active', 'inactive', 'left'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    simulation = relationship("Simulation", back_populates="agents")


# ============================================================================
# Legacy: SimulationState (kept for backward compatibility during migration)
# ============================================================================

class SimulationState(Base):
    """Legacy table - will be removed after migration."""
    __tablename__ = "simulation_state"
    id = Column(Integer, primary_key=True)
    loop_count = Column(Integer, default=0)
    active_agent_count = Column(Integer, default=0)


# ============================================================================
# Database Connection
# ============================================================================

# Handle SQLite vs PostgreSQL connection args
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# LocalBBS → Multi-User Cloud Deployment Plan

## Overview

This document outlines the migration plan for deploying LocalBBS on **Railway** with the following major changes:

1. **Database Migration**: SQLite → PostgreSQL
2. **Multi-tenancy**: Support multiple users, each with their own isolated simulations
3. **Authentication**: User registration & login
4. **Storage**: File-based agent storage → Database-based storage
5. **Scalability**: Single-threaded simulation → Background workers

---

## Current Architecture Analysis

### What We Have Now

```
┌─────────────────────────────────────────────────────────┐
│                    Single User App                       │
├─────────────────────────────────────────────────────────┤
│  FastAPI Server (main.py)                               │
│    └─ Background Thread (simulation loop)               │
│                                                         │
│  Data Storage:                                          │
│    ├─ SQLite (data/forum.db) - Posts, Threads, State    │
│    └─ File System (agents/) - Agent personas & memory   │
│                                                         │
│  Global Singletons:                                     │
│    ├─ simulation (Simulation class)                     │
│    ├─ settings (Settings class)                         │
│    └─ llm_client (LLMClient)                            │
└─────────────────────────────────────────────────────────┘
```

### What We Need

```
┌─────────────────────────────────────────────────────────┐
│                  Multi-User Cloud App                    │
├─────────────────────────────────────────────────────────┤
│  FastAPI Server (Stateless)                             │
│    └─ Handles HTTP requests only                        │
│                                                         │
│  PostgreSQL Database:                                   │
│    ├─ Users (auth, API keys)                            │
│    ├─ Simulations (per-user)                            │
│    ├─ Threads & Posts (linked to simulation)            │
│    └─ Agents (persona, memory stored in DB)             │
│                                                         │
│  Background Workers:                                    │
│    └─ Per-simulation async workers                      │
│                                                         │
│  Session/Auth:                                          │
│    └─ JWT or Session-based authentication               │
└─────────────────────────────────────────────────────────┘
```

---

## Phase 1: Database Migration (SQLite → PostgreSQL)

### 1.1 Update Dependencies

Add to `requirements.txt`:
```
psycopg2-binary      # PostgreSQL adapter
asyncpg              # Async PostgreSQL (optional, for async queries)
alembic              # Database migrations
```

### 1.2 Update Configuration

Update `config.py`:
```python
import os

class Settings:
    # Database - Railway provides DATABASE_URL automatically
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/forum.db")
    
    # Handle Railway's postgres:// vs postgresql:// URL format
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # ... rest of settings
```

### 1.3 Update Database Connection

Update `database.py`:
```python
from sqlalchemy import create_engine
from config import settings

# Remove SQLite-specific connect_args
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
```

### 1.4 Schema Changes for PostgreSQL Compatibility

- Change `String` to `String(255)` for indexed columns
- Ensure `Text` is used for large content fields
- Add explicit length constraints where needed

---

## Phase 2: Multi-User Data Model

### 2.1 New Database Schema

```sql
-- Users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    openrouter_api_key VARCHAR(255),  -- User's own API key (encrypted)
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- Simulations (replaces global simulation state)
CREATE TABLE simulations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    topic VARCHAR(500) NOT NULL,
    language VARCHAR(50) DEFAULT 'English',
    pool_style VARCHAR(50) DEFAULT 'professional',
    status VARCHAR(20) DEFAULT 'stopped',  -- 'running', 'stopped', 'paused'
    loop_count INTEGER DEFAULT 0,
    consecutive_idle_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Settings (per-simulation, inheriting from user defaults)
    model_name VARCHAR(255) DEFAULT 'google/gemini-2.5-flash-lite-preview-09-2025',
    max_loops INTEGER DEFAULT 500,
    loop_delay FLOAT DEFAULT 2.0,
    agent_count INTEGER DEFAULT 10,
    enable_web_browse BOOLEAN DEFAULT TRUE,
    web_browse_safety_mode VARCHAR(50) DEFAULT 'safebrowsing'
);

-- Threads (now linked to simulation)
CREATE TABLE threads (
    id SERIAL PRIMARY KEY,
    simulation_id INTEGER REFERENCES simulations(id) ON DELETE CASCADE,
    title VARCHAR(500),
    created_at TIMESTAMP DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'active'
);

-- Posts (unchanged, linked via thread)
CREATE TABLE posts (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER REFERENCES threads(id) ON DELETE CASCADE,
    agent_name VARCHAR(255),
    content TEXT,
    parent_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    likes INTEGER DEFAULT 0
);

-- Agents (previously file-based, now in DB)
CREATE TABLE agents (
    id SERIAL PRIMARY KEY,
    simulation_id INTEGER REFERENCES simulations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    directory_name VARCHAR(255) NOT NULL,  -- For display/reference
    agent_md TEXT,           -- Persona (was AGENT.md)
    memory_md TEXT,          -- Memory (was MEMORY.md)
    temp_md TEXT,            -- Ephemeral context (was TEMP.md)
    last_read_post_id INTEGER DEFAULT 0,  -- Was in state.json
    status VARCHAR(20) DEFAULT 'active',  -- 'active', 'inactive', 'left'
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(simulation_id, directory_name)
);

-- User Settings (persistent preferences - saved and restored on login)
CREATE TABLE user_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    
    -- LLM Settings
    default_model VARCHAR(255) DEFAULT 'google/gemini-2.5-flash-lite-preview-09-2025',
    
    -- Simulation Defaults
    default_agent_count INTEGER DEFAULT 10,
    default_max_loops INTEGER DEFAULT 500,
    default_loop_delay FLOAT DEFAULT 2.0,
    default_pool_style VARCHAR(50) DEFAULT 'professional',
    mother_intervention_threshold INTEGER DEFAULT 5,
    mother_lookback_k INTEGER DEFAULT 25,
    
    -- Web Browsing Settings
    enable_web_browse BOOLEAN DEFAULT TRUE,
    web_browse_safety_mode VARCHAR(50) DEFAULT 'safebrowsing',
    web_browse_timeout INTEGER DEFAULT 10,
    google_safe_browsing_api_key VARCHAR(255),
    
    -- UI Preferences
    theme VARCHAR(20) DEFAULT 'light',
    posts_per_page INTEGER DEFAULT 50,
    auto_scroll BOOLEAN DEFAULT TRUE,
    
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 2.2 SQLAlchemy Models Update

Update `database.py` with new models:

```python
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100))
    openrouter_api_key = Column(String(255))  # Encrypted
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    simulations = relationship("Simulation", back_populates="user")
    settings = relationship("UserSettings", back_populates="user", uselist=False)

class Simulation(Base):
    __tablename__ = "simulations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    topic = Column(String(500), nullable=False)
    language = Column(String(50), default="English")
    pool_style = Column(String(50), default="professional")
    status = Column(String(20), default="stopped")
    loop_count = Column(Integer, default=0)
    consecutive_idle_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Per-simulation settings
    model_name = Column(String(255), default="google/gemini-2.5-flash-lite-preview-09-2025")
    max_loops = Column(Integer, default=500)
    loop_delay = Column(Float, default=2.0)
    agent_count = Column(Integer, default=10)
    enable_web_browse = Column(Boolean, default=True)
    web_browse_safety_mode = Column(String(50), default="safebrowsing")
    
    user = relationship("User", back_populates="simulations")
    threads = relationship("Thread", back_populates="simulation")
    agents = relationship("Agent", back_populates="simulation")

class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True, index=True)
    simulation_id = Column(Integer, ForeignKey("simulations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    directory_name = Column(String(255), nullable=False)
    agent_md = Column(Text)
    memory_md = Column(Text)
    temp_md = Column(Text)
    last_read_post_id = Column(Integer, default=0)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    simulation = relationship("Simulation", back_populates="agents")

class UserSettings(Base):
    __tablename__ = "user_settings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    
    # LLM Settings
    default_model = Column(String(255), default="google/gemini-2.5-flash-lite-preview-09-2025")
    
    # Simulation Defaults
    default_agent_count = Column(Integer, default=10)
    default_max_loops = Column(Integer, default=500)
    default_loop_delay = Column(Float, default=2.0)
    default_pool_style = Column(String(50), default="professional")
    mother_intervention_threshold = Column(Integer, default=5)
    mother_lookback_k = Column(Integer, default=25)
    
    # Web Browsing Settings
    enable_web_browse = Column(Boolean, default=True)
    web_browse_safety_mode = Column(String(50), default="safebrowsing")
    web_browse_timeout = Column(Integer, default=10)
    google_safe_browsing_api_key = Column(String(255))
    
    # UI Preferences
    theme = Column(String(20), default="light")
    posts_per_page = Column(Integer, default=50)
    auto_scroll = Column(Boolean, default=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="settings")

# Update Thread and Post to link to Simulation
class Thread(Base):
    __tablename__ = "threads"
    id = Column(Integer, primary_key=True, index=True)
    simulation_id = Column(Integer, ForeignKey("simulations.id"), nullable=False)
    title = Column(String(500), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")
    
    simulation = relationship("Simulation", back_populates="threads")
    posts = relationship("Post", back_populates="thread")
```

---

## Phase 3: Authentication System

### 3.1 Add Authentication Dependencies

```
python-jose[cryptography]  # JWT tokens
passlib[bcrypt]            # Password hashing
```

### 3.2 Create Auth Module (`auth.py`)

```python
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
```

### 3.3 Auth Endpoints

```python
@app.post("/api/auth/register")
async def register(email: str, password: str, db: Session = Depends(get_db)):
    # Check if user exists
    # Create user with hashed password
    # Return token

@app.post("/api/auth/login")
async def login(email: str, password: str, db: Session = Depends(get_db)):
    # Verify credentials
    # Return token

@app.get("/api/auth/me")
async def get_me(user: User = Depends(get_current_user)):
    # Return current user info
```

### 3.4 Session-Based Alternative (Simpler)

For a web-first experience, consider using session cookies instead of JWT:

```python
from starlette.middleware.sessions import SessionMiddleware

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
```

---

## Phase 4: Refactor Simulation for Multi-User

### 4.1 Remove Global Singletons

Current problem areas:
- `simulation = Simulation()` in `simulation.py` (global singleton)
- `settings = Settings()` in `config.py` (global singleton)
- `llm_client = LLMClient()` in `llm_client.py` (global singleton)

Solution: Create instances per-simulation or per-user.

### 4.2 Simulation Manager

Create `simulation_manager.py`:

```python
import asyncio
from typing import Dict
from database import Simulation as SimulationModel

class SimulationRunner:
    """Handles running a single simulation."""
    
    def __init__(self, simulation_id: int):
        self.simulation_id = simulation_id
        self.running = False
        self._task = None
    
    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
    
    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
    
    async def _run_loop(self):
        while self.running:
            try:
                await self.step()
            except Exception as e:
                logger.error(f"Simulation {self.simulation_id} error: {e}")
            await asyncio.sleep(self.loop_delay)
    
    async def step(self):
        # Refactored step logic using simulation_id
        pass

class SimulationManager:
    """Manages all running simulations."""
    
    def __init__(self):
        self.runners: Dict[int, SimulationRunner] = {}
    
    async def start_simulation(self, simulation_id: int):
        if simulation_id in self.runners:
            return  # Already running
        
        runner = SimulationRunner(simulation_id)
        self.runners[simulation_id] = runner
        await runner.start()
    
    async def stop_simulation(self, simulation_id: int):
        if simulation_id in self.runners:
            await self.runners[simulation_id].stop()
            del self.runners[simulation_id]

# Global manager (OK to be global, it manages per-user simulations)
simulation_manager = SimulationManager()
```

### 4.3 Per-User LLM Client

```python
class LLMClient:
    def __init__(self, api_key: str, model: str):
        self.client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.model = model
    
    # ... methods unchanged

def get_llm_client_for_simulation(simulation: SimulationModel) -> LLMClient:
    """Get LLM client configured for a specific simulation."""
    api_key = simulation.user.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    model = simulation.model_name
    return LLMClient(api_key, model)
```

### 4.4 Update Agent Class for DB Storage

```python
class Agent:
    """Agent that stores data in PostgreSQL instead of files."""
    
    def __init__(self, agent_record: AgentModel):
        self.record = agent_record
        self.id = agent_record.id
        self.name = agent_record.name
    
    def read_agent_md(self) -> str:
        return self.record.agent_md or ""
    
    def read_memory_md(self) -> str:
        return self.record.memory_md or ""
    
    def read_temp_md(self) -> str:
        return self.record.temp_md or ""
    
    def write_memory_md(self, content: str, db: Session):
        self.record.memory_md = content
        db.commit()
    
    def write_temp_md(self, content: str, db: Session):
        self.record.temp_md = content
        db.commit()
    
    def get_last_read_id(self) -> int:
        return self.record.last_read_post_id or 0
    
    def set_last_read_id(self, post_id: int, db: Session):
        self.record.last_read_post_id = post_id
        db.commit()
```

---

## Phase 5: API Redesign

### 5.1 New API Structure

```
/api/auth/
    POST   /register          - Create account
    POST   /login             - Login
    GET    /me                - Current user info
    PUT    /me                - Update profile
    PUT    /me/api-key        - Update OpenRouter API key

/api/simulations/
    GET    /                  - List user's simulations
    POST   /                  - Create new simulation
    GET    /{id}              - Get simulation details
    DELETE /{id}              - Delete simulation
    POST   /{id}/start        - Start simulation
    POST   /{id}/stop         - Stop simulation
    POST   /{id}/reset        - Reset (clear posts, keep agents)
    PUT    /{id}/settings     - Update simulation settings

/api/simulations/{id}/posts/
    GET    /                  - Get all posts (tree structure)
    GET    /json              - Get posts as JSON

/api/simulations/{id}/agents/
    GET    /                  - List agents
    GET    /{agent_id}        - Get agent details
    POST   /                  - Manually add agent (optional)

/api/simulations/{id}/export/
    GET    /json              - Export as JSON
    GET    /html              - Export as HTML
    POST   /import            - Import from JSON

/api/settings/
    GET    /                  - Get user settings (persistent)
    PUT    /                  - Update user settings (auto-saved to DB)

---

## Phase 6.5: Persistent User Settings

### 6.5.1 Settings Service

Create `services/settings_service.py`:

```python
from database import UserSettings, User
from sqlalchemy.orm import Session

class SettingsService:
    @staticmethod
    def get_user_settings(db: Session, user_id: int) -> UserSettings:
        """Get user settings, creating defaults if not exists."""
        settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id)
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings
    
    @staticmethod
    def update_user_settings(db: Session, user_id: int, **kwargs) -> UserSettings:
        """Update user settings. All changes are persisted immediately."""
        settings = SettingsService.get_user_settings(db, user_id)
        for key, value in kwargs.items():
            if hasattr(settings, key) and value is not None:
                setattr(settings, key, value)
        db.commit()
        db.refresh(settings)
        return settings
    
    @staticmethod
    def apply_to_new_simulation(settings: UserSettings, simulation) -> None:
        """Apply user's default settings to a new simulation."""
        simulation.model_name = settings.default_model
        simulation.agent_count = settings.default_agent_count
        simulation.max_loops = settings.default_max_loops
        simulation.loop_delay = settings.default_loop_delay
        simulation.enable_web_browse = settings.enable_web_browse
        simulation.web_browse_safety_mode = settings.web_browse_safety_mode
```

### 6.5.2 Settings API Endpoints

```python
from pydantic import BaseModel
from typing import Optional

class UserSettingsUpdate(BaseModel):
    default_model: Optional[str] = None
    default_agent_count: Optional[int] = None
    default_max_loops: Optional[int] = None
    default_loop_delay: Optional[float] = None
    default_pool_style: Optional[str] = None
    mother_intervention_threshold: Optional[int] = None
    mother_lookback_k: Optional[int] = None
    enable_web_browse: Optional[bool] = None
    web_browse_safety_mode: Optional[str] = None
    web_browse_timeout: Optional[int] = None
    google_safe_browsing_api_key: Optional[str] = None
    theme: Optional[str] = None
    posts_per_page: Optional[int] = None
    auto_scroll: Optional[bool] = None

class UserSettingsResponse(BaseModel):
    default_model: str
    default_agent_count: int
    default_max_loops: int
    default_loop_delay: float
    default_pool_style: str
    mother_intervention_threshold: int
    mother_lookback_k: int
    enable_web_browse: bool
    web_browse_safety_mode: str
    web_browse_timeout: int
    google_safe_browsing_api_key: Optional[str]  # Masked in response
    theme: str
    posts_per_page: int
    auto_scroll: bool

@app.get("/api/settings", response_model=UserSettingsResponse)
async def get_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user's persistent settings."""
    settings = SettingsService.get_user_settings(db, user.id)
    return UserSettingsResponse(
        default_model=settings.default_model,
        default_agent_count=settings.default_agent_count,
        default_max_loops=settings.default_max_loops,
        default_loop_delay=settings.default_loop_delay,
        default_pool_style=settings.default_pool_style,
        mother_intervention_threshold=settings.mother_intervention_threshold,
        mother_lookback_k=settings.mother_lookback_k,
        enable_web_browse=settings.enable_web_browse,
        web_browse_safety_mode=settings.web_browse_safety_mode,
        web_browse_timeout=settings.web_browse_timeout,
        google_safe_browsing_api_key="***" if settings.google_safe_browsing_api_key else None,
        theme=settings.theme,
        posts_per_page=settings.posts_per_page,
        auto_scroll=settings.auto_scroll,
    )

@app.put("/api/settings", response_model=UserSettingsResponse)
async def update_settings(
    settings_update: UserSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user's persistent settings. Changes are saved immediately."""
    updated = SettingsService.update_user_settings(
        db, user.id, **settings_update.dict(exclude_unset=True)
    )
    return updated
```

### 6.5.3 Settings Persistence Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                    User Settings Lifecycle                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. USER REGISTERS                                                │
│     └─▶ Default UserSettings row created                          │
│                                                                   │
│  2. USER LOGS IN                                                  │
│     └─▶ Settings loaded from DB into session/context              │
│                                                                   │
│  3. USER CHANGES SETTING (via UI)                                 │
│     └─▶ PUT /api/settings                                         │
│         └─▶ Immediately saved to PostgreSQL                       │
│             └─▶ UI updated with confirmation                      │
│                                                                   │
│  4. USER CREATES NEW SIMULATION                                   │
│     └─▶ User's default settings applied to simulation             │
│         (model, agent_count, loop_delay, etc.)                    │
│                                                                   │
│  5. USER LOGS OUT / CLOSES BROWSER                                │
│     └─▶ Settings already persisted (no action needed)             │
│                                                                   │
│  6. USER LOGS IN AGAIN (same device or different)                 │
│     └─▶ All settings restored exactly as they were                │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 6.5.4 Settings Modal Update

Update `templates/settings_modal.html` to:
- Load current settings on open (GET /api/settings)
- Auto-save on change (PUT /api/settings) 
- Show "Saved!" confirmation
- Handle API key securely (show masked, only update if changed)

```html
<!-- Example: Auto-save on input change -->
<input type="number" 
       name="default_agent_count" 
       value="{{ settings.default_agent_count }}"
       hx-put="/api/settings"
       hx-trigger="change"
       hx-vals='js:{"default_agent_count": parseInt(this.value)}'
       hx-swap="none"
       hx-on::after-request="showSavedToast()">
```

---

## Phase 6.7: Email Verification & Admin System

### 6.7.1 Overview

Add email verification for new user registration and an admin dashboard for global settings management.

**Key Features:**
- Email verification using Resend API
- Admin dashboard for user management
- Global settings (API keys, registration controls)
- First registered user becomes admin automatically

### 6.7.2 Database Changes

**User Model Updates:**
```python
class User(Base):
    # ... existing fields ...
    is_admin = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    verification_token = Column(String(255), unique=True, nullable=True)
    verification_token_expires = Column(DateTime, nullable=True)
```

**New GlobalSettings Model:**
```python
class GlobalSettings(Base):
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True)
    
    # API Keys (global, not per-user)
    google_safe_browsing_api_key = Column(String(255))
    resend_api_key = Column(String(255))
    
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
```

### 6.7.3 Email Service

Create `services/email_service.py` using Resend API:

```python
class EmailService:
    RESEND_API_URL = "https://api.resend.com/emails"
    
    async def send_verification_email(self, user: User) -> bool:
        # Generate token, store in user
        # Send email with verification link
        # Returns True if successful
    
    def verify_token(self, token: str) -> Optional[User]:
        # Validate token and return user if valid
    
    def mark_user_verified(self, user: User) -> None:
        # Set is_verified = True, clear token
```

### 6.7.4 Registration Flow

```
┌────────────────────────────────────────────────────────────┐
│                    Registration Flow                        │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  1. User submits registration form                         │
│     ├─ If first user: Auto-verify, make admin → Dashboard  │
│     └─ Otherwise: Create unverified account                │
│                                                            │
│  2. Send verification email (if required)                  │
│     └─ Email contains unique verification link             │
│                                                            │
│  3. User clicks verification link                          │
│     └─ /verify-email?token=xxx                             │
│         ├─ Valid: Mark verified → Login page               │
│         └─ Invalid/Expired: Error page with resend option  │
│                                                            │
│  4. User logs in                                           │
│     ├─ If not verified: Redirect to pending page           │
│     └─ If verified: Continue to dashboard                  │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 6.7.5 Admin Dashboard Features

**User Management:**
- View all users with status (verified, active, admin)
- Toggle user active/disabled
- Toggle admin status
- Manually verify users
- Delete users (cascades all their data)

**Global Settings:**
- Resend API key (for email)
- Google Safe Browsing API key (shared by all users)
- Email from address/name
- Toggle email verification requirement
- Toggle registration open/closed
- Max simulations per user

### 6.7.6 Admin Routes

```python
@app.get("/admin")
async def admin_dashboard(admin: User = Depends(get_current_admin_user)):
    # Render admin dashboard

@app.put("/api/admin/users/{user_id}/toggle-active")
@app.put("/api/admin/users/{user_id}/toggle-admin")
@app.put("/api/admin/users/{user_id}/verify")
@app.delete("/api/admin/users/{user_id}")

@app.get("/api/admin/settings")
@app.put("/api/admin/settings")
```

### 6.7.7 Environment Variables

Add to `.env.example`:
```bash
# Email (can also be set in Admin Dashboard)
RESEND_API_KEY=re_xxxxx
APP_URL=https://your-app.railway.app
EMAIL_FROM_ADDRESS=noreply@yourdomain.com
EMAIL_FROM_NAME=LocalBBS
```

---

## Phase 7: Frontend Updates

### 7.1 New Pages Needed

1. **Landing/Login Page** (`templates/login.html`)
   - Email/password login
   - Link to register

2. **Registration Page** (`templates/register.html`)
   - Email, password, display name
   - Optional: OpenRouter API key

3. **Dashboard** (`templates/dashboard.html`)
   - List of user's simulations
   - Create new simulation button
   - Quick stats per simulation

4. **Simulation View** (update `templates/index.html`)
   - Scoped to single simulation
   - Add simulation selector in header
   - Back to dashboard link

### 7.2 UI Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
│   Login     │────▶│  Dashboard  │────▶│  Simulation View    │
│             │     │             │     │  (existing UI)      │
└─────────────┘     │ - My Sims   │     │                     │
       │            │ - New Sim   │     │  Topic: [...]       │
       │            │             │     │  [Start] [Stop]     │
       ▼            └─────────────┘     │                     │
┌─────────────┐            │            │  Posts...           │
│  Register   │            │            │  Agents sidebar     │
│             │────────────┘            └─────────────────────┘
└─────────────┘
```

---

## Phase 8: Railway Deployment

### 8.1 Required Files

**`Procfile`**:
```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

**`runtime.txt`** (optional):
```
python-3.11
```

**`railway.toml`** (optional):
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 100
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

### 8.2 Environment Variables (Railway)

Set these in Railway dashboard:

```
DATABASE_URL          - Auto-provided by Railway PostgreSQL
SECRET_KEY            - Generate a secure random string
OPENROUTER_API_KEY    - Default/fallback API key (optional)
GOOGLE_SAFE_BROWSING_API_KEY - For web browsing safety (optional)
```

### 8.3 Database Provisioning

1. Add PostgreSQL plugin in Railway dashboard
2. Railway automatically provides `DATABASE_URL`
3. Run migrations on first deploy:
   ```
   alembic upgrade head
   ```

### 8.4 Static Files

Railway serves static files directly, but for production consider:
- Using a CDN (CloudFlare, etc.)
- Or keep FastAPI static serving (fine for low-medium traffic)

---

## Phase 9: Migration Strategy

### 9.1 Database Migrations with Alembic

Initialize Alembic:
```bash
alembic init alembic
```

Update `alembic/env.py`:
```python
from database import Base
target_metadata = Base.metadata
```

Create initial migration:
```bash
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

### 9.2 Deployment Steps

1. **Create Railway Project**
   - Connect GitHub repo
   - Add PostgreSQL service

2. **Set Environment Variables**
   - Add required secrets

3. **Deploy**
   - Railway auto-deploys on push
   - Monitor logs for issues

4. **Run Migrations**
   - Use Railway CLI or shell
   - `alembic upgrade head`

---

## Implementation Order

### Week 1: Database & Core Changes
- [ ] Add PostgreSQL support to config
- [ ] Update database.py with new models (User, UserSettings, Simulation, Agent)
- [ ] Set up Alembic migrations
- [ ] Test locally with PostgreSQL (via Docker)

### Week 2: Multi-User Data Model
- [ ] Create User, Simulation, Agent models
- [ ] Update Thread/Post to link to Simulation
- [ ] Migrate Agent class from file-based to DB-based
- [ ] Update Mother class for DB-based agents

### Week 3: Authentication & User Settings
- [ ] Implement auth.py
- [ ] Create login/register endpoints
- [ ] Add session/token middleware
- [ ] Create login/register pages
- [ ] Implement SettingsService for persistent user settings
- [ ] Create settings API endpoints (GET/PUT /api/settings)
- [ ] Auto-create default settings on user registration

### Week 4: Simulation Manager
- [ ] Create SimulationManager
- [ ] Refactor Simulation class for per-user instances
- [ ] Apply user's default settings to new simulations
- [ ] Update LLM client for per-simulation API keys
- [ ] Test concurrent simulations

### Week 5: API & Frontend
- [ ] Implement new API endpoints
- [ ] Create dashboard page
- [ ] Create/update settings modal with auto-save
- [ ] Update existing templates for multi-user
- [ ] Add simulation selector

### Week 6: Deployment & Testing
- [ ] Create Procfile, railway.toml
- [ ] Deploy to Railway
- [ ] Test with multiple users
- [ ] Verify settings persistence across sessions
- [ ] Performance tuning

---

## Security Considerations

1. **API Key Storage**: Encrypt user API keys at rest
2. **Rate Limiting**: Add per-user rate limits
3. **Input Validation**: Sanitize all user inputs
4. **CORS**: Configure properly for production
5. **HTTPS**: Railway provides SSL automatically
6. **SQL Injection**: SQLAlchemy ORM handles this
7. **XSS**: Jinja2 auto-escapes, but be careful with `|safe`

---

## Cost Considerations

### Railway Pricing
- **Hobby**: $5/month - Good for development/testing
- **Pro**: $20/month - Better for production
- PostgreSQL: Included in plan limits

### OpenRouter Costs
- Users provide their own API keys (ideal)
- Or provide a fallback key with usage limits

### Optimization Tips
- Cache LLM responses where possible
- Implement simulation pause for inactive users
- Add daily/monthly usage limits per user

---

## Future Enhancements

1. **Real-time Updates**: WebSocket for live post updates
2. **Collaboration**: Shared simulations between users
3. **Templates**: Pre-made agent pools users can select
4. **Analytics**: Track interesting discussions, engagement
5. **API Access**: Allow programmatic access to simulations
6. **Webhooks**: Notify users of interesting events
7. **Mobile App**: React Native or PWA

---

## Files to Modify/Create

### Modify
- `config.py` - PostgreSQL support, remove globals
- `database.py` - New models (User, UserSettings, Simulation, Agent), PostgreSQL compatibility
- `main.py` - Auth, scoped routes, API endpoints, settings endpoints
- `simulation.py` - DB-based agents, per-simulation instances
- `llm_client.py` - Per-user initialization
- `requirements.txt` - New dependencies
- `templates/index.html` - Scoped to simulation
- `templates/thread.html` - No changes needed
- `templates/settings_modal.html` - Per-user persistent settings with auto-save

### Create
- `auth.py` - Authentication logic
- `services/settings_service.py` - Persistent user settings management
- `simulation_manager.py` - Manages running simulations
- `alembic/` - Database migrations
- `templates/login.html` - Login page
- `templates/register.html` - Registration page
- `templates/dashboard.html` - User dashboard
- `templates/settings.html` - Dedicated settings page (optional)
- `Procfile` - Railway deployment
- `railway.toml` - Railway config (optional)

---

## Local Development on macOS (MacBook Air)

### Prerequisites

1. **Homebrew** (if not installed):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Python 3.11+**:
   ```bash
   brew install python@3.11
   ```

3. **PostgreSQL** (choose ONE option):

#### Option A: PostgreSQL via Homebrew (Recommended for M1/M2 Macs)

```bash
# Install PostgreSQL
brew install postgresql@15

# Start PostgreSQL service
brew services start postgresql@15

# Create database and user
createdb localbbs
psql -d localbbs -c "CREATE USER localbbs_user WITH PASSWORD 'localbbs_pass';"
psql -d localbbs -c "GRANT ALL PRIVILEGES ON DATABASE localbbs TO localbbs_user;"
psql -d localbbs -c "ALTER DATABASE localbbs OWNER TO localbbs_user;"

# Connection URL for this setup:
# DATABASE_URL="postgresql://localbbs_user:localbbs_pass@localhost:5432/localbbs"
```

#### Option B: PostgreSQL via Docker

```bash
# Install Docker Desktop for Mac first: https://docs.docker.com/desktop/mac/install/

# Run PostgreSQL container
docker run --name localbbs-postgres \
  -e POSTGRES_USER=localbbs_user \
  -e POSTGRES_PASSWORD=localbbs_pass \
  -e POSTGRES_DB=localbbs \
  -p 5432:5432 \
  -d postgres:15

# To stop/start later:
docker stop localbbs-postgres
docker start localbbs-postgres

# To view logs:
docker logs localbbs-postgres
```

#### Option C: Postgres.app (GUI option)

1. Download from https://postgresapp.com/
2. Install and open the app
3. Click "Initialize" to create a new server
4. Create database via psql:
   ```bash
   /Applications/Postgres.app/Contents/Versions/latest/bin/psql -p5432
   CREATE DATABASE localbbs;
   ```

---

### Step-by-Step Local Setup

```bash
# 1. Navigate to project
cd /Users/kyunghyuncho/Repos/agent-forum

# 2. Create virtual environment (if not exists)
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies (including new ones)
pip install -r requirements.txt

# 4. Set environment variables
# Add these to your ~/.zshrc or create a .env file

export DATABASE_URL="postgresql://localbbs_user:localbbs_pass@localhost:5432/localbbs"
export SECRET_KEY="dev-secret-key-change-this-in-production"
export OPENROUTER_API_KEY="your-openrouter-api-key"

# Optional (for web browsing safety):
export GOOGLE_SAFE_BROWSING_API_KEY="your-google-api-key"

# 5. Initialize database (after implementing migrations)
alembic upgrade head

# 6. Run the development server
uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 7. Open in browser
open http://127.0.0.1:8000
```

---

### Using a .env File (Recommended)

Create a `.env` file in the project root:

```bash
# .env (DO NOT COMMIT THIS FILE)
DATABASE_URL=postgresql://localbbs_user:localbbs_pass@localhost:5432/localbbs
SECRET_KEY=dev-secret-key-change-this-in-production
OPENROUTER_API_KEY=sk-or-your-key-here
GOOGLE_SAFE_BROWSING_API_KEY=your-google-api-key
```

Add `.env` to `.gitignore`:
```bash
echo ".env" >> .gitignore
```

Install python-dotenv and update `config.py`:
```python
# At the top of config.py
from dotenv import load_dotenv
load_dotenv()
```

Add to `requirements.txt`:
```
python-dotenv
```

---

### Testing the Multi-User Flow Locally

1. **Start the server**:
   ```bash
   source .venv/bin/activate
   uvicorn main:app --reload --port 8000
   ```

2. **Register two test users** (in different browser windows/incognito):
   - User 1: `test1@example.com`
   - User 2: `test2@example.com`

3. **Test isolation**:
   - User 1: Create a simulation with topic "AI Ethics"
   - User 2: Create a simulation with topic "Climate Change"
   - Verify each user only sees their own simulations

4. **Test settings persistence**:
   - Change settings as User 1 (model, agent count, etc.)
   - Log out and log back in
   - Verify settings are preserved

5. **Test concurrent simulations**:
   - Start simulation for User 1
   - Start simulation for User 2
   - Monitor that both run independently

---

### Useful Development Commands

```bash
# Check PostgreSQL is running (Homebrew)
brew services list | grep postgresql

# Connect to local database
psql -d localbbs -U localbbs_user

# View all tables
\dt

# View users
SELECT * FROM users;

# View simulations
SELECT * FROM simulations;

# Reset database (CAUTION: deletes all data)
psql -d localbbs -U localbbs_user -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
alembic upgrade head

# View server logs with more detail
uvicorn main:app --reload --port 8000 --log-level debug

# Run with multiple workers (for testing concurrency)
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 4
```

---

### Troubleshooting on macOS

**Issue: `psycopg2` installation fails**
```bash
# Install with binary package instead
pip install psycopg2-binary
```

**Issue: PostgreSQL connection refused**
```bash
# Check if PostgreSQL is running
brew services list
# or
docker ps

# Restart PostgreSQL
brew services restart postgresql@15
# or
docker restart localbbs-postgres
```

**Issue: Permission denied on database**
```bash
psql -d postgres -c "ALTER USER localbbs_user WITH SUPERUSER;"
```

**Issue: Port 5432 already in use**
```bash
# Find what's using the port
lsof -i :5432

# Kill the process or use a different port
docker run ... -p 5433:5432 ...
# Then update DATABASE_URL to use port 5433
```

**Issue: M1/M2 Mac compatibility**
- Use Homebrew PostgreSQL (native ARM) instead of Docker x86 images
- Or ensure Docker Desktop is using Apple Silicon virtualization

---

## Quick Start for Development

```bash
# 1. Start local PostgreSQL (Docker)
docker run --name localbbs-postgres -e POSTGRES_PASSWORD=password -e POSTGRES_DB=localbbs -p 5432:5432 -d postgres:15

# 2. Set environment
export DATABASE_URL="postgresql://postgres:password@localhost:5432/localbbs"
export SECRET_KEY="dev-secret-key-change-in-production"

# 3. Install new dependencies
pip install -r requirements.txt

# 4. Run migrations
alembic upgrade head

# 5. Start server
uvicorn main:app --reload --port 8000
```

---

This plan provides a comprehensive roadmap for transforming LocalBBS into a multi-user cloud application. The phased approach allows for incremental development and testing while maintaining a working application throughout the migration.

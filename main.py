import threading
import time
import os
import json
import shutil
import re
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db, init_db, Post, Thread, User, UserSettings, Simulation, Agent, GlobalSettings
from config import settings
from auth import (
    UserCreate, UserLogin, Token, UserResponse,
    create_user, authenticate_user, create_access_token, 
    get_current_user, get_current_user_optional, get_current_user_html,
    user_to_response, get_user_by_email, update_user_api_key, 
    get_current_admin_user, get_current_admin_user_html, LoginRequired
)
from services.settings_service import (
    SettingsService, UserSettingsUpdate, UserSettingsResponse, settings_service
)
from services.email_service import EmailService, get_email_service

# Import simulation modules
from simulation import simulation  # Legacy single-user
from simulation_manager import simulation_manager  # Multi-user manager

# --- Background Task for Legacy Single-User Mode ---
def run_legacy_loop():
    while True:
        try:
            simulation.step()
        except Exception as e:
            # print(f"Loop Error: {e}")
            pass
        time.sleep(settings.LOOP_DELAY)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database
    init_db()
    
    # Initialize global settings if they don't exist
    db = next(get_db())
    try:
        global_settings = db.query(GlobalSettings).first()
        if not global_settings:
            global_settings = GlobalSettings()
            db.add(global_settings)
            
        # Sync potentially missing settings from env vars
        if not global_settings.resend_api_key and settings.RESEND_API_KEY:
            global_settings.resend_api_key = settings.RESEND_API_KEY
            print(f"Initialized Resend API key from environment")
            
        if (not global_settings.app_url or global_settings.app_url == "http://localhost:8000") and settings.APP_URL != "http://localhost:8000":
            global_settings.app_url = settings.APP_URL
            print(f"Initialized App URL from environment: {settings.APP_URL}")
            
        if not global_settings.google_safe_browsing_api_key and settings.GOOGLE_SAFE_BROWSING_API_KEY:
            global_settings.google_safe_browsing_api_key = settings.GOOGLE_SAFE_BROWSING_API_KEY
            print(f"Initialized Google Safe Browsing API key from environment")
            
        db.commit()
    finally:
        db.close()
    
    # Start multi-user simulation manager
    simulation_manager.start()
    
    # Start legacy simulation thread (for legacy single-user mode)
    legacy_thread = threading.Thread(target=run_legacy_loop, daemon=True)
    legacy_thread.start()
    
    yield
    
    # Cleanup
    simulation_manager.stop()

app = FastAPI(title="LocalBBS", lifespan=lifespan)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    """Redirect to login page when authentication is required."""
    if request.headers.get("HX-Request"):
        return Response(headers={"HX-Redirect": "/login"})
    
    return RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER
    )

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ============================================================================
# Health Check (for Railway and monitoring)
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and monitoring."""
    return {"status": "healthy", "service": "localbbs"}


# ============================================================================
# Email Verification Routes
# ============================================================================

@app.get("/verify-pending", response_class=HTMLResponse)
async def verify_pending_page(request: Request, email: str = ""):
    """Show email verification pending page."""
    return templates.TemplateResponse("verify_pending.html", {
        "request": request,
        "email": email
    })


@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email(
    request: Request,
    token: str,
    db: Session = Depends(get_db)
):
    """Verify email with token from email link."""
    email_service = get_email_service(db)
    user = email_service.verify_token(token)
    
    if not user:
        return templates.TemplateResponse("verify_result.html", {
            "request": request,
            "success": False,
            "message": "Invalid or expired verification link. Please request a new one."
        })
    
    email_service.mark_user_verified(user)
    
    return templates.TemplateResponse("verify_result.html", {
        "request": request,
        "success": True,
        "message": "Your email has been verified! You can now log in."
    })


@app.post("/api/auth/resend-verification")
async def resend_verification(
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    """Resend verification email."""
    user = get_user_by_email(db, email)
    
    if not user:
        # Don't reveal if email exists
        return {"status": "ok", "message": "If the email exists, a verification link has been sent."}
    
    if user.is_verified:
        return {"status": "ok", "message": "Email is already verified. Please log in."}
    
    email_service = get_email_service(db)
    await email_service.send_verification_email(user)
    
    return {"status": "ok", "message": "Verification email sent. Please check your inbox."}


# ============================================================================
# Admin Routes
# ============================================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin: User = Depends(get_current_admin_user_html),
    db: Session = Depends(get_db)
):
    """Admin dashboard."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    global_settings = db.query(GlobalSettings).first()
    
    # Get some stats
    total_simulations = db.query(Simulation).count()
    total_posts = db.query(Post).count()
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "admin": admin,
        "users": users,
        "global_settings": global_settings,
        "stats": {
            "total_users": len(users),
            "verified_users": sum(1 for u in users if u.is_verified),
            "total_simulations": total_simulations,
            "total_posts": total_posts
        }
    })


@app.get("/api/admin/users")
async def api_admin_list_users(
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """List all users (admin only)."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [user_to_response(u) for u in users]


@app.put("/api/admin/users/{user_id}/toggle-active")
async def api_admin_toggle_user_active(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Enable/disable a user account (admin only)."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot disable your own account"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_active = not user.is_active
    db.commit()
    
    return {"status": "ok", "is_active": user.is_active}


@app.put("/api/admin/users/{user_id}/toggle-admin")
async def api_admin_toggle_user_admin(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Make/remove admin status for a user (admin only)."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify your own admin status"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_admin = not user.is_admin
    db.commit()
    
    return {"status": "ok", "is_admin": user.is_admin}


@app.put("/api/admin/users/{user_id}/verify")
async def api_admin_verify_user(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Manually verify a user's email (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    db.commit()
    
    return {"status": "ok", "is_verified": True}


@app.delete("/api/admin/users/{user_id}")
async def api_admin_delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Delete a user and all their data (admin only)."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.delete(user)
    db.commit()
    
    return {"status": "ok", "message": "User deleted"}


@app.get("/api/admin/settings")
async def api_admin_get_global_settings(
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get global application settings (admin only)."""
    global_settings = db.query(GlobalSettings).first()
    if not global_settings:
        global_settings = GlobalSettings()
        db.add(global_settings)
        db.commit()
        db.refresh(global_settings)
    
    return {
        "google_safe_browsing_api_key": global_settings.google_safe_browsing_api_key or "",
        "resend_api_key": global_settings.resend_api_key or "",
        "email_from_address": global_settings.email_from_address,
        "email_from_name": global_settings.email_from_name,
        "require_email_verification": global_settings.require_email_verification,
        "allow_registration": global_settings.allow_registration,
        "app_name": global_settings.app_name,
        "app_url": global_settings.app_url or "",
        "max_simulations_per_user": global_settings.max_simulations_per_user
    }


@app.put("/api/admin/settings")
async def api_admin_update_global_settings(
    google_safe_browsing_api_key: str = Form(None),
    resend_api_key: str = Form(None),
    email_from_address: str = Form(None),
    email_from_name: str = Form(None),
    require_email_verification: bool = Form(True),
    allow_registration: bool = Form(True),
    app_name: str = Form(None),
    app_url: str = Form(None),
    max_simulations_per_user: int = Form(5),
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Update global application settings (admin only)."""
    global_settings = db.query(GlobalSettings).first()
    if not global_settings:
        global_settings = GlobalSettings()
        db.add(global_settings)
    
    # Update fields
    if google_safe_browsing_api_key is not None:
        global_settings.google_safe_browsing_api_key = google_safe_browsing_api_key or None
    if resend_api_key is not None:
        global_settings.resend_api_key = resend_api_key or None
    if email_from_address is not None:
        global_settings.email_from_address = email_from_address
    if email_from_name is not None:
        global_settings.email_from_name = email_from_name
    global_settings.require_email_verification = require_email_verification
    global_settings.allow_registration = allow_registration
    if app_name is not None:
        global_settings.app_name = app_name
    if app_url is not None:
        global_settings.app_url = app_url or None
    global_settings.max_simulations_per_user = max_simulations_per_user
    global_settings.updated_by = admin.id
    
    db.commit()
    
    return {"status": "ok", "message": "Settings updated"}


# ============================================================================
# Authentication Routes
# ============================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Render registration page."""
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/api/auth/register")
async def api_register(
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(None),
    db: Session = Depends(get_db)
):
    """Register a new user."""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"Registration attempt: email={email}")
    
    # Check if registration is allowed
    global_settings = db.query(GlobalSettings).first()
    if global_settings and not global_settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is currently disabled"
        )
    
    # Check if user exists
    existing_user = get_user_by_email(db, email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Determine if email verification is required
    require_verification = True
    if global_settings:
        require_verification = global_settings.require_email_verification
    
    # Create user (first user is auto-verified and admin)
    user_data = UserCreate(email=email, password=password, display_name=display_name)
    is_first_user = db.query(User).count() == 0
    logger.info(f"Creating user: is_first_user={is_first_user}, require_verification={require_verification}")
    
    user = create_user(db, user_data, auto_verify=not require_verification or is_first_user)
    logger.info(f"User created: id={user.id}, is_admin={user.is_admin}, is_verified={user.is_verified}")
    
    # Send verification email if required and not first user
    if require_verification and not is_first_user:
        email_service = get_email_service(db)
        await email_service.send_verification_email(user)
        # Redirect to verification pending page
        return RedirectResponse(url=f"/verify-pending?email={email}", status_code=status.HTTP_303_SEE_OTHER)
    
    # Create access token
    access_token = create_access_token(data={"sub": str(user.id)})
    logger.info(f"Access token created for user {user.id}, redirecting to /dashboard")
    
    # Return response with cookie
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    is_production = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("SECRET_KEY", "").startswith("dev-") == False
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,  # 1 week
        samesite="lax",
        secure=is_production  # Only send cookie over HTTPS in production
    )
    return response


@app.post("/api/auth/login")
async def api_login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Login and get access token."""
    user = authenticate_user(db, email, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    # Check if user is verified (unless verification is disabled)
    global_settings = db.query(GlobalSettings).first()
    if global_settings and global_settings.require_email_verification:
        if not user.is_verified:
            return RedirectResponse(
                url=f"/verify-pending?email={email}&not_verified=1",
                status_code=status.HTTP_303_SEE_OTHER
            )
    
    # Check if account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been disabled. Please contact an administrator."
        )
    
    # Create access token
    access_token = create_access_token(data={"sub": str(user.id)})
    
    # Return response with cookie
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    is_production = os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("SECRET_KEY", "").startswith("dev-") == False
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,  # 1 week
        samesite="lax",
        secure=is_production  # Only send cookie over HTTPS in production
    )
    return response


@app.post("/api/auth/logout")
async def api_logout():
    """Logout and clear cookie."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@app.get("/api/auth/me")
async def api_get_me(user: User = Depends(get_current_user)):
    """Get current user info."""
    return user_to_response(user)


# ============================================================================
# User Settings Routes
# ============================================================================

@app.get("/api/settings", response_model=UserSettingsResponse)
async def api_get_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user's persistent settings."""
    user_settings = settings_service.get_user_settings(db, user.id)
    return settings_service.to_response(user_settings)


@app.put("/api/settings", response_model=UserSettingsResponse)
async def api_update_settings(
    settings_update: UserSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user's persistent settings."""
    updated = settings_service.update_user_settings(
        db, user.id, **settings_update.dict(exclude_unset=True)
    )
    return settings_service.to_response(updated)


@app.put("/api/auth/api-key")
async def api_update_api_key(
    api_key: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user's OpenRouter API key."""
    import logging
    logger = logging.getLogger(__name__)
    
    if not api_key or not api_key.strip():
        logger.warning(f"Attempted to save empty API key for user {user.id}")
        return {"status": "error", "message": "API key cannot be empty"}
        
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "too_short"
    logger.info(f"Updating API key for user {user.id}: {masked_key}")
    
    # Update key
    update_user_api_key(db, user, api_key)
    
    # Verify save
    db.expire(user)
    db.refresh(user)
    saved_key = user.openrouter_api_key
    logger.info(f"Verify API key for user {user.id}: {'Set' if saved_key else 'Not Set'} (Len: {len(saved_key) if saved_key else 0})")
    
    return {"status": "ok", "message": "API key updated"}


# ============================================================================
# Dashboard & Simulation Management (Multi-User)
# ============================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user_html),
    db: Session = Depends(get_db)
):
    """User dashboard showing their simulations."""
    
    # Debug logging for API key persistence
    import logging
    logger = logging.getLogger(__name__)
    key_status = "SET" if user.openrouter_api_key else "NOT SET" 
    masked_key = f" (Len: {len(user.openrouter_api_key)})" if user.openrouter_api_key else ""
    logger.info(f"Dashboard load - User {user.id} API Key: {key_status}{masked_key}")
    
    simulations = db.query(Simulation).filter(Simulation.user_id == user.id).order_by(Simulation.created_at.desc()).all()
    user_settings = settings_service.get_user_settings(db, user.id)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "simulations": simulations,
        "settings": user_settings
    })


@app.post("/api/simulations")
async def api_create_simulation(
    topic: str = Form(...),
    user: User = Depends(get_current_user_html),
    db: Session = Depends(get_db)
):
    """Create a new simulation."""
    import logging
    logger = logging.getLogger(__name__)
    
    # Get user's default settings
    user_settings = settings_service.get_user_settings(db, user.id)
    logger.info(f"Creating simulation for user {user.id}: default_pool_style = {user_settings.default_pool_style}")
    
    # Detect language from topic
    from simulation import detect_language
    language = detect_language(topic)
    
    # Create simulation
    sim = Simulation(
        user_id=user.id,
        topic=topic,
        language=language,
        pool_style=user_settings.default_pool_style,
        status="stopped"
    )
    
    # Apply user's default settings
    settings_service.apply_to_new_simulation(db, user_settings, sim)
    logger.info(f"After apply_to_new_simulation: sim.pool_style = {sim.pool_style}")
    
    db.add(sim)
    db.commit()
    db.refresh(sim)
    
    return RedirectResponse(url=f"/simulation/{sim.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/simulation/{sim_id}", response_class=HTMLResponse)
async def view_simulation(
    request: Request,
    sim_id: int,
    user: User = Depends(get_current_user_html),
    db: Session = Depends(get_db)
):
    """View a specific simulation."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    return templates.TemplateResponse("simulation.html", {
        "request": request,
        "user": user,
        "simulation": sim,
        "settings": settings
    })


@app.delete("/api/simulations/{sim_id}")
async def api_delete_simulation(
    sim_id: int,
    user: User = Depends(get_current_user_html),
    db: Session = Depends(get_db)
):
    """Delete a simulation."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    db.delete(sim)
    db.commit()
    
    return {"status": "ok", "message": "Simulation deleted"}


# ============================================================================
# Simulation Control API (Multi-User)
# ============================================================================

@app.post("/api/simulations/{sim_id}/start", response_class=HTMLResponse)
async def api_start_simulation(
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start a simulation."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Update status
    sim.status = "running"
    db.commit()
    
    # TODO: In future, trigger the simulation manager to start this simulation
    # For now, we'll handle this in a background task
    
    return HTMLResponse(content=f'''
        <button 
            class="bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded" 
            hx-post="/api/simulations/{sim_id}/stop" 
            hx-target="#control-panel" 
            hx-swap="innerHTML">
            Stop
        </button>
        <span class="flex h-3 w-3 relative">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span>
        </span>
    ''')


@app.post("/api/simulations/{sim_id}/stop", response_class=HTMLResponse)
async def api_stop_simulation(
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stop a simulation."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Update status
    sim.status = "stopped"
    db.commit()
    
    return HTMLResponse(content=f'''
        <button 
            class="bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded" 
            hx-post="/api/simulations/{sim_id}/start" 
            hx-target="#control-panel" 
            hx-swap="innerHTML">
            Start
        </button>
    ''')


@app.get("/api/simulations/{sim_id}/posts/json")
async def api_get_simulation_posts_json(
    sim_id: int,
    after_id: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get posts for a simulation as JSON, optionally filtered by after_id."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Get thread for this simulation
    thread = db.query(Thread).filter(Thread.simulation_id == sim_id).first()
    
    if not thread:
        return {"posts": [], "max_id": 0}
    
    # Get posts, optionally filtered by after_id
    query = db.query(Post).filter(Post.thread_id == thread.id)
    if after_id > 0:
        query = query.filter(Post.id > after_id)
    posts = query.order_by(Post.created_at.asc()).all()
    
    # Get max ID from all posts (not just filtered)
    max_id_result = db.query(Post.id).filter(Post.thread_id == thread.id).order_by(Post.id.desc()).first()
    max_id = max_id_result[0] if max_id_result else 0
    
    posts_data = [{
        "id": p.id,
        "agent_name": p.agent_name,
        "content": p.content,
        "likes": p.likes,
        "created_at": p.created_at.isoformat(),
        "parent_id": p.parent_id
    } for p in posts]
    
    return {"posts": posts_data, "max_id": max_id}


@app.get("/api/simulations/{sim_id}/posts", response_class=HTMLResponse)
async def api_get_simulation_posts(
    request: Request,
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get posts for a simulation as HTML."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Get thread for this simulation
    thread = db.query(Thread).filter(Thread.simulation_id == sim_id).first()
    
    if not thread:
        return HTMLResponse(content='''
            <div class="text-center py-8 text-gray-500">
                <p>No posts yet. Start the simulation to generate discussion.</p>
            </div>
        ''')
    
    # Get posts
    posts = db.query(Post).filter(Post.thread_id == thread.id).order_by(Post.created_at.asc()).all()
    
    if not posts:
        return HTMLResponse(content='''
            <div class="text-center py-8 text-gray-500">
                <p>Waiting for agents to start posting...</p>
            </div>
        ''')
    
    # Build tree structure
    post_map = {p.id: {"post": p, "children": []} for p in posts}
    root_nodes = []

    for p in posts:
        node = post_map[p.id]
        if p.parent_id and p.parent_id in post_map:
            post_map[p.parent_id]["children"].append(node)
        else:
            root_nodes.append(node)
    
    return templates.TemplateResponse("thread.html", {"request": request, "nodes": root_nodes})


@app.get("/api/simulations/{sim_id}/agents", response_class=HTMLResponse)
async def api_get_simulation_agents(
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get agents for a simulation."""
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    agents = db.query(Agent).filter(
        Agent.simulation_id == sim_id,
        Agent.status == "active"
    ).all()
    
    if not agents:
        return HTMLResponse(content='''
            <div class="flex flex-col items-center justify-center py-8 text-center">
                <div class="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center mb-2">
                    <svg class="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                    </svg>
                </div>
                <p class="text-slate-500 text-sm">No agents yet</p>
                <p class="text-slate-400 text-xs mt-0.5">Start to spawn agents</p>
            </div>
        ''')
    
    # Avatar gradient classes
    gradients = [
        'background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);',
        'background: linear-gradient(135deg, #ec4899 0%, #f43f5e 100%);',
        'background: linear-gradient(135deg, #14b8a6 0%, #10b981 100%);',
        'background: linear-gradient(135deg, #f97316 0%, #f59e0b 100%);',
        'background: linear-gradient(135deg, #3b82f6 0%, #0ea5e9 100%);',
        'background: linear-gradient(135deg, #8b5cf6 0%, #a855f7 100%);',
    ]
    
    html = '<div class="space-y-2">'
    for i, agent in enumerate(agents):
        gradient = gradients[i % len(gradients)]
        html += f'''
        <div class="group p-3 rounded-xl cursor-pointer hover:bg-slate-50 transition-all duration-200 flex items-center gap-3" 
             hx-get="/api/simulations/{sim_id}/agents/{agent.id}" 
             hx-target="#agent-modal-content" 
             hx-trigger="click">
            <div class="h-9 w-9 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-sm ring-2 ring-white" style="{gradient}">
                {agent.name[:2].upper()}
            </div>
            <div class="flex-1 min-w-0">
                <span class="text-sm font-medium text-slate-700 group-hover:text-slate-900 truncate block">{agent.name}</span>
                <span class="text-xs text-slate-400">Active</span>
            </div>
            <svg class="w-4 h-4 text-slate-300 group-hover:text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
        </div>
        '''
    html += '</div>'
    return HTMLResponse(content=html)


@app.get("/api/simulations/{sim_id}/agents/{agent_id}", response_class=HTMLResponse)
async def api_get_agent_details(
    request: Request,
    sim_id: int,
    agent_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get agent details."""
    agent = db.query(Agent).filter(
        Agent.id == agent_id,
        Agent.simulation_id == sim_id
    ).first()
    
    if not agent:
        return HTMLResponse(content='<div class="p-4">Agent not found</div>')
    
    # Verify user owns the simulation
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        return HTMLResponse(content='<div class="p-4">Access denied</div>')
    
    return templates.TemplateResponse("agent_view.html", {
        "request": request,
        "name": agent.name,
        "agent_md": agent.agent_md or "",
        "memory_md": agent.memory_md or ""
    })



# ============================================================================
# Simulation Export Routes (Multi-User)
# ============================================================================

@app.get("/api/simulations/{sim_id}/export/json")
async def api_export_simulation_json(
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Export a simulation as JSON."""
    # Verify user owns the simulation
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Get thread for this simulation
    thread = db.query(Thread).filter(Thread.simulation_id == sim_id).first()
    
    # Get posts
    posts = []
    if thread:
        posts = db.query(Post).filter(Post.thread_id == thread.id).order_by(Post.created_at.asc()).all()
    
    # Serialize posts
    posts_data = [{
        "id": p.id,
        "agent": p.agent_name,
        "content": p.content,
        "likes": p.likes,
        "created_at": p.created_at.isoformat(),
        "parent_id": p.parent_id
    } for p in posts]
    
    # Get agents from database (not filesystem)
    agents = db.query(Agent).filter(
        Agent.simulation_id == sim_id,
        Agent.status == "active"
    ).all()
    
    agents_data = {}
    for agent in agents:
        agents_data[agent.name] = {
            "agent_md": agent.agent_md or "",
            "memory_md": agent.memory_md or ""
        }
    
    export_data = {
        "simulation_id": sim.id,
        "topic": sim.topic,
        "language": sim.language,
        "pool_style": sim.pool_style,
        "posts": posts_data,
        "agents": agents_data,
        "exported_at": time.time()
    }
    
    # Return JSON response with download headers
    response = JSONResponse(content=export_data)
    filename = f"simulation_{sim_id}_{int(time.time())}.json"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.get("/api/simulations/{sim_id}/export/html", response_class=HTMLResponse)
async def api_export_simulation_html(
    request: Request,
    sim_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Export a simulation as standalone HTML."""
    # Verify user owns the simulation
    sim = db.query(Simulation).filter(
        Simulation.id == sim_id,
        Simulation.user_id == user.id
    ).first()
    
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    
    # Get thread and posts
    thread = db.query(Thread).filter(Thread.simulation_id == sim_id).first()
    
    posts = []
    if thread:
        posts = db.query(Post).filter(Post.thread_id == thread.id).order_by(Post.created_at.asc()).all()
    
    # Build tree structure
    post_map = {p.id: {"post": p, "children": []} for p in posts}
    root_nodes = []
    
    for p in posts:
        node = post_map[p.id]
        if p.parent_id and p.parent_id in post_map:
            post_map[p.parent_id]["children"].append(node)
        else:
            root_nodes.append(node)
    
    # Get agents from database
    agents = db.query(Agent).filter(
        Agent.simulation_id == sim_id,
        Agent.status == "active"
    ).all()
    
    agents_data = [{
        "name": agent.name,
        "status": "Active",
        "model": sim.model_name or "Unknown",
        "system_prompt": agent.agent_md or "",
        "agent_md": agent.agent_md or "",
        "memory_md": agent.memory_md or ""
    } for agent in agents]
    
    return templates.TemplateResponse("export_static.html", {
        "request": request,
        "nodes": root_nodes,
        "agents": agents_data,
        "topic": sim.topic
    })


# ============================================================================
# Legacy Routes (Single-User Mode - for backward compatibility)
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """
    Root route - redirects to dashboard if logged in, 
    otherwise redirects to login page.
    """
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_mode(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Legacy single-user mode (accessible at /legacy for backward compatibility).
    """
    thread = db.query(Thread).filter(Thread.simulation_id == None).first()
    topic = thread.title if thread else ""
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "topic": topic, 
        "simulation_running": simulation.running,
        "settings": settings,
        "user": None
    })

@app.post("/start")
async def start_simulation_legacy(
    topic: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Require authentication
):
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
async def stop_simulation_legacy(user: User = Depends(get_current_user)):  # Require authentication
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
async def reset_simulation(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Require authentication
):
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
    safe_browsing_api_key: str = Form(""),
    user: User = Depends(get_current_user)  # Require authentication
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
async def export_json(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Require authentication
):
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
async def import_json(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Require authentication
):
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
    # Security: validate agent name to prevent path traversal
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail="Invalid agent name")
    
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

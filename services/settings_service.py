"""
Settings service for managing persistent user settings.
"""

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import UserSettings, User, Simulation


# ============================================================================
# Pydantic Models for API
# ============================================================================

class UserSettingsUpdate(BaseModel):
    """Model for updating user settings."""
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
    theme: Optional[str] = None
    posts_per_page: Optional[int] = None
    auto_scroll: Optional[bool] = None


class UserSettingsResponse(BaseModel):
    """Model for settings API response."""
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
    theme: str
    posts_per_page: int
    auto_scroll: bool

    class Config:
        from_attributes = True


# ============================================================================
# Settings Service
# ============================================================================

class SettingsService:
    """Service for managing user settings."""
    
    @staticmethod
    def get_user_settings(db: Session, user_id: int) -> UserSettings:
        """
        Get user settings, creating defaults if not exists.
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            UserSettings instance
        """
        settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id)
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings
    
    @staticmethod
    def update_user_settings(db: Session, user_id: int, **kwargs) -> UserSettings:
        """
        Update user settings. All changes are persisted immediately.
        
        Args:
            db: Database session
            user_id: User ID
            **kwargs: Settings to update
            
        Returns:
            Updated UserSettings instance
        """
        import logging
        logger = logging.getLogger(__name__)
        
        settings = SettingsService.get_user_settings(db, user_id)
        logger.info(f"Updating settings for user {user_id}: {kwargs}")
        
        for key, value in kwargs.items():
            if hasattr(settings, key) and value is not None:
                old_value = getattr(settings, key)
                setattr(settings, key, value)
                logger.info(f"  Setting {key}: {old_value} -> {value}")
        
        db.commit()
        db.refresh(settings)
        logger.info(f"  After commit: default_pool_style = {settings.default_pool_style}")
        return settings
    
    @staticmethod
    def apply_to_new_simulation(db: Session, user_settings: UserSettings, simulation: Simulation) -> None:
        """
        Apply user's default settings to a new simulation.
        
        Args:
            db: Database session
            user_settings: User's settings
            simulation: Simulation to apply settings to
        """
        simulation.model_name = user_settings.default_model
        simulation.agent_count = user_settings.default_agent_count
        simulation.max_loops = user_settings.default_max_loops
        simulation.loop_delay = user_settings.default_loop_delay
        simulation.pool_style = user_settings.default_pool_style
        simulation.enable_web_browse = user_settings.enable_web_browse
        simulation.web_browse_safety_mode = user_settings.web_browse_safety_mode
        db.commit()
    
    @staticmethod
    def to_response(settings: UserSettings) -> UserSettingsResponse:
        """
        Convert UserSettings model to response model.
        
        Args:
            settings: UserSettings instance
            
        Returns:
            UserSettingsResponse
        """
        return UserSettingsResponse(
            default_model=settings.default_model or "google/gemini-2.5-flash-lite-preview-09-2025",
            default_agent_count=settings.default_agent_count or 3,
            default_max_loops=settings.default_max_loops or 50,
            default_loop_delay=settings.default_loop_delay or 2.0,
            default_pool_style=settings.default_pool_style or "professional",
            mother_intervention_threshold=settings.mother_intervention_threshold or 5,
            mother_lookback_k=settings.mother_lookback_k or 25,
            enable_web_browse=settings.enable_web_browse if settings.enable_web_browse is not None else True,
            web_browse_safety_mode=settings.web_browse_safety_mode or "safebrowsing",
            web_browse_timeout=settings.web_browse_timeout or 10,
            theme=settings.theme or "light",
            posts_per_page=settings.posts_per_page or 50,
            auto_scroll=settings.auto_scroll if settings.auto_scroll is not None else True,
        )
    
    @staticmethod
    def get_effective_settings(db: Session, user: User, simulation: Optional[Simulation] = None) -> Dict[str, Any]:
        """
        Get effective settings for a user/simulation context.
        Simulation settings override user defaults when available.
        
        Args:
            db: Database session
            user: User instance
            simulation: Optional simulation instance
            
        Returns:
            Dictionary of effective settings
        """
        user_settings = SettingsService.get_user_settings(db, user.id)
        
        if simulation:
            return {
                "model_name": simulation.model_name,
                "agent_count": simulation.agent_count,
                "max_loops": simulation.max_loops,
                "loop_delay": simulation.loop_delay,
                "pool_style": simulation.pool_style,
                "enable_web_browse": simulation.enable_web_browse,
                "web_browse_safety_mode": simulation.web_browse_safety_mode,
                "web_browse_timeout": user_settings.web_browse_timeout,
                "mother_intervention_threshold": user_settings.mother_intervention_threshold,
                "mother_lookback_k": user_settings.mother_lookback_k,
                "openrouter_api_key": user.openrouter_api_key,
            }
        else:
            return {
                "model_name": user_settings.default_model,
                "agent_count": user_settings.default_agent_count,
                "max_loops": user_settings.default_max_loops,
                "loop_delay": user_settings.default_loop_delay,
                "pool_style": user_settings.default_pool_style,
                "enable_web_browse": user_settings.enable_web_browse,
                "web_browse_safety_mode": user_settings.web_browse_safety_mode,
                "web_browse_timeout": user_settings.web_browse_timeout,
                "mother_intervention_threshold": user_settings.mother_intervention_threshold,
                "mother_lookback_k": user_settings.mother_lookback_k,
                "openrouter_api_key": user.openrouter_api_key,
            }


# Singleton instance for convenience
settings_service = SettingsService()

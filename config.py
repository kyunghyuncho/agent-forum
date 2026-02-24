import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

class Settings:
    # Database - supports both SQLite (local dev) and PostgreSQL (production)
    # Railway provides DATABASE_URL automatically
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/forum.db")
    
    # Handle Railway's postgres:// vs postgresql:// URL format
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # Authentication
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-please-change-in-production")
    
    # Security check: warn/fail if using default secret key in production
    if SECRET_KEY == "dev-secret-key-please-change-in-production":
        if os.getenv("RAILWAY_ENVIRONMENT"):
            raise RuntimeError(
                "SECURITY ERROR: You must set SECRET_KEY environment variable in production! "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week
    
    # OpenRouter / LLM settings
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    
    # Default model, can be overridden by user settings
    MODEL_NAME = "google/gemini-2.5-flash" 
    MAX_LOOPS = 50
    DEFAULT_AGENT_COUNT = 3
    MOTHER_LOOKBACK_K = 25
    LOOP_DELAY = 2.0
    
    # Agent pool style: "professional", "creative", or "fun"
    AGENT_POOL_STYLE = "professional"
    
    # MOTHER intervention settings
    # If agents do nothing for this many consecutive turns, MOTHER intervenes
    MOTHER_INTERVENTION_THRESHOLD = 5
    
    # Web browsing settings
    ENABLE_WEB_BROWSE = True  # On by default
    WEB_BROWSE_TIMEOUT = 10    # Request timeout in seconds
    
    # URL Safety Mode: "allowlist" or "safebrowsing" (default)
    # - allowlist: Only allow URLs from the curated list below (most restrictive)
    # - safebrowsing: Use Google Safe Browsing API to check URL safety (more permissive)
    WEB_BROWSE_SAFETY_MODE = os.getenv("WEB_BROWSE_SAFETY_MODE", "safebrowsing")
    
    # Resend API Key for Email Verification
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    
    # App URL for email links (e.g. https://myapp.railway.app)
    # If not set, defaults to http://localhost:8000
    # Railway provides RAILWAY_PUBLIC_DOMAIN dynamically
    APP_URL = os.getenv("APP_URL")
    if not APP_URL and os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        APP_URL = f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN')}"
    if not APP_URL:
        APP_URL = "http://localhost:8000"
    
    # Google Safe Browsing API key (required if SAFETY_MODE is "safebrowsing")
    # Get your free API key at: https://console.cloud.google.com/apis/library/safebrowsing.googleapis.com
    GOOGLE_SAFE_BROWSING_API_KEY = os.getenv("GOOGLE_SAFE_BROWSING_API_KEY", "")
    
    # Always blocked domains (applied in both modes)
    WEB_BROWSE_BLOCKED_DOMAINS = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    ]
    
    WEB_BROWSE_ALLOWED_DOMAINS = [
        # Encyclopedias & Research
        "wikipedia.org",
        "arxiv.org",
        "pubmed.ncbi.nlm.nih.gov",
        "plato.stanford.edu",
        "who.int",
        # Journals
        "nature.com",
        "science.org",
        # News
        "reuters.com",
        "apnews.com",
        "bbc.com",
        # Note: All .gov and .edu domains are also allowed (see web_browser.py)
    ]

settings = Settings()

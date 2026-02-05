import os

class Settings:
    DATABASE_URL = "sqlite:///./data/forum.db"
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    # Default model, can be overridden by user
    MODEL_NAME = "google/gemini-2.5-flash-lite-preview-09-2025" 
    MAX_LOOPS = 500
    DEFAULT_AGENT_COUNT = 10
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

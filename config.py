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
    
    # Web browsing settings
    ENABLE_WEB_BROWSE = True  # On by default
    WEB_BROWSE_TIMEOUT = 10    # Request timeout in seconds
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

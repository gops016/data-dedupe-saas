import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CACHE_DIR = BASE_DIR / "cache"

# Ensure directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# SQLite cache settings
SQLITE_DB_PATH = str(CACHE_DIR / "data_cache.db")

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# App configurations
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB max file size
CHUNK_SIZE = 1024 * 1024  # 1MB upload chunks

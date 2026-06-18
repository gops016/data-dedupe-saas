import os
import logging
from typing import Dict, Any, List, Optional
import polars as pl
import redis
from backend.config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD, CACHE_DIR

logger = logging.getLogger("dedupe_cache")
logging.basicConfig(level=logging.INFO)

class CacheInterface:
    def set_records(self, session_id: str, df: pl.DataFrame) -> None:
        raise NotImplementedError

    def set_metadata(self, session_id: str, metadata: Dict[str, Any]) -> None:
        raise NotImplementedError

    def get_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_page(self, session_id: str, page: int, page_size: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_total_count(self, session_id: str) -> int:
        raise NotImplementedError

    def get_metrics(self, session_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_dataframe(self, session_id: str) -> Optional[pl.DataFrame]:
        raise NotImplementedError

    def has_session(self, session_id: str) -> bool:
        raise NotImplementedError


class RedisCache(CacheInterface):
    def __init__(self):
        try:
            self.client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                socket_timeout=3
            )
            self.client.ping()
            self.is_connected = True
            logger.info("Successfully connected to Redis cache.")
        except Exception as e:
            self.is_connected = False
            logger.warning(f"Redis connection failed: {e}. Falling back to FileCache.")

    def set_records(self, session_id: str, df: pl.DataFrame) -> None:
        import io
        buffer = io.BytesIO()
        df.write_ipc(buffer)
        data = buffer.getvalue()
        # Set with 1 hour expiration
        self.client.setex(f"session:{session_id}", 3600, data)

    def set_metadata(self, session_id: str, metadata: Dict[str, Any]) -> None:
        import json
        self.client.setex(f"metadata:{session_id}", 3600, json.dumps(metadata))

    def get_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        import json
        data = self.client.get(f"metadata:{session_id}")
        if not data:
            return None
        return json.loads(data)

    def get_dataframe(self, session_id: str) -> Optional[pl.DataFrame]:
        data = self.client.get(f"session:{session_id}")
        if not data:
            return None
        import io
        return pl.read_ipc(io.BytesIO(data))

    def get_page(self, session_id: str, page: int, page_size: int) -> List[Dict[str, Any]]:
        df = self.get_dataframe(session_id)
        if df is None:
            return []
        
        start = page * page_size
        sliced_df = df.slice(start, page_size)
        return sliced_df.to_dicts()

    def get_total_count(self, session_id: str) -> int:
        df = self.get_dataframe(session_id)
        return len(df) if df is not None else 0

    def get_metrics(self, session_id: str) -> Dict[str, Any]:
        df = self.get_dataframe(session_id)
        if df is None:
            return {}
        
        # Calculate metrics using Polars
        total_rows = len(df)
        
        # We assume processing pipeline sets these boolean flags
        invalid_phone_count = df.filter(~pl.col("is_valid_phone")).height if "is_valid_phone" in df.columns else 0
        invalid_email_count = df.filter(~pl.col("is_valid_email")).height if "is_valid_email" in df.columns else 0
        duplicate_count = df.filter(pl.col("is_duplicate")).height if "is_duplicate" in df.columns else 0
        
        # Count duplicate clusters (unique cluster_ids excluding None/empty)
        if "cluster_id" in df.columns:
            clusters = df.filter(pl.col("cluster_id").is_not_null() & (pl.col("cluster_id") != ""))
            unique_clusters = clusters.select("cluster_id").n_unique()
        else:
            unique_clusters = 0

        return {
            "total_rows": total_rows,
            "invalid_phone_count": invalid_phone_count,
            "invalid_email_count": invalid_email_count,
            "duplicate_count": duplicate_count,
            "unique_clusters": unique_clusters
        }

    def has_session(self, session_id: str) -> bool:
        return bool(self.client.exists(f"session:{session_id}"))


class FileCache(CacheInterface):
    """Fallback cache that stores Polars DataFrames as high-performance IPC files on disk."""
    def __init__(self):
        self.directory = CACHE_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized FileCache directory at {self.directory}")

    def _get_path(self, session_id: str) -> str:
        return str(self.directory / f"{session_id}.ipc")

    def _get_metadata_path(self, session_id: str) -> str:
        return str(self.directory / f"{session_id}.json")

    def set_records(self, session_id: str, df: pl.DataFrame) -> None:
        path = self._get_path(session_id)
        df.write_ipc(path)

    def set_metadata(self, session_id: str, metadata: Dict[str, Any]) -> None:
        import json
        path = self._get_metadata_path(session_id)
        with open(path, "w") as f:
            json.dump(metadata, f)

    def get_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        import json
        path = self._get_metadata_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    def get_dataframe(self, session_id: str) -> Optional[pl.DataFrame]:
        path = self._get_path(session_id)
        if not os.path.exists(path):
            return None
        # Use memory mapping for ultra-fast load
        return pl.read_ipc(path, memory_map=True)

    def get_page(self, session_id: str, page: int, page_size: int) -> List[Dict[str, Any]]:
        df = self.get_dataframe(session_id)
        if df is None:
            return []
        
        start = page * page_size
        sliced_df = df.slice(start, page_size)
        return sliced_df.to_dicts()

    def get_total_count(self, session_id: str) -> int:
        df = self.get_dataframe(session_id)
        return len(df) if df is not None else 0

    def get_metrics(self, session_id: str) -> Dict[str, Any]:
        df = self.get_dataframe(session_id)
        if df is None:
            return {}
        
        total_rows = len(df)
        invalid_phone_count = df.filter(~pl.col("is_valid_phone")).height if "is_valid_phone" in df.columns else 0
        invalid_email_count = df.filter(~pl.col("is_valid_email")).height if "is_valid_email" in df.columns else 0
        duplicate_count = df.filter(pl.col("is_duplicate")).height if "is_duplicate" in df.columns else 0
        
        if "cluster_id" in df.columns:
            clusters = df.filter(pl.col("cluster_id").is_not_null() & (pl.col("cluster_id") != ""))
            unique_clusters = clusters.select("cluster_id").n_unique()
        else:
            unique_clusters = 0

        return {
            "total_rows": total_rows,
            "invalid_phone_count": invalid_phone_count,
            "invalid_email_count": invalid_email_count,
            "duplicate_count": duplicate_count,
            "unique_clusters": unique_clusters
        }

    def has_session(self, session_id: str) -> bool:
        return os.path.exists(self._get_path(session_id))


# Instantiate cache based on availability
redis_cache = RedisCache()
if redis_cache.is_connected:
    cache = redis_cache
else:
    cache = FileCache()

def get_cache() -> CacheInterface:
    return cache

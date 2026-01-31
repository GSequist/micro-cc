import time
import threading
import os
from typing import Dict, Optional, Any


class RedisStateManager:
    """
    Thread-safe in-memory state manager replacing Redis.
    Maintains same interface as RedisStateManager for drop-in replacement.
    Shared state across all instances via class variables.
    """
    # Class variables shared across all instances
    _store: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()  # Reentrant lock for thread safety
    _cleanup_running = False

    def __init__(self):
        self.app_name = os.getenv("REDIS_APP_KEY", "micro_cc")

    def _make_key(self, key_type: str, *parts: str) -> str:
        """Generate namespaced key"""
        return f"{self.app_name}:{key_type}:" + ":".join(parts)

    def _set_with_ttl(self, key: str, value: Any, ttl: int):
        """Set value with expiration timestamp"""
        expiry = time.time() + ttl
        with self._lock:
            self._store[key] = {"value": value, "expiry": expiry}

    def _get(self, key: str) -> Optional[Any]:
        """Get value if not expired, cleanup if expired"""
        with self._lock:
            if key not in self._store:
                return None
            entry = self._store[key]
            if time.time() > entry["expiry"]:
                del self._store[key]
                return None
            return entry["value"]

    def _delete(self, key: str):
        """Delete key"""
        with self._lock:
            self._store.pop(key, None)

    def _cleanup_expired(self):
        """Background task to clean up expired entries"""
        while self._cleanup_running:
            time.sleep(60)  # Run every minute
            current_time = time.time()
            with self._lock:
                expired_keys = [
                    k for k, v in self._store.items() if current_time > v["expiry"]
                ]
                for key in expired_keys:
                    del self._store[key]
                if expired_keys:
                    print(f"Cleaned up {len(expired_keys)} expired state entries")

    def start_cleanup_task(self):
        """Start background cleanup thread"""
        if not self._cleanup_running:
            self._cleanup_running = True
            cleanup_thread = threading.Thread(
                target=self._cleanup_expired, daemon=True, name="StateCleanup"
            )
            cleanup_thread.start()
            print("State cleanup task started")

    def stop_cleanup_task(self):
        """Stop background cleanup thread"""
        self._cleanup_running = False

    # ========================= Plan Data =========================

    def set_plan(self, project_dir:str, plan: str) -> None:
        """Set the plan for a user"""
        try:
            key = self._make_key("plan", project_dir)
            self._set_with_ttl(key, plan, 3600)
        except Exception as e:
            print(f"State error in set_plan: {e}")

    def get_plan(self, project_dir: str) -> str:
        """Get the plan for a user"""
        try:
            key = self._make_key("plan",project_dir)
            return self._get(key)
        except Exception as e:
            print(f"State error in get_plan: {e}")
            return None

    ##########################tool discovery

    def get_discovered_tools(self, project_dir: str) -> set:
        try:
            key = self._make_key("discovered_tools", project_dir)
            data = self._get(key)
            if not data:
                return set()
            return set(data.get("discovered_tools", []))
        except Exception as e:
            print(f"State error in get_discovered_tools: {e}")
            return set()

    def add_discovered_tools(self, project_dir: str, tools: list):
        try:
            existing = self.get_discovered_tools(project_dir)
            existing.update(tools)
            data = {"discovered_tools": list(existing)}
            key = self._make_key("discovered_tools", project_dir)
            self._set_with_ttl(key, data, 3600)
        except Exception as e:
            print(f"State error in add_discovered_tools: {e}")

    def clear_discovered_tools(self, project_dir: str):
        try:
            key = self._make_key("discovered_tools", project_dir)
            self._delete(key)
        except Exception as e:
            print(f"State error in clear_discovered_tools: {e}")

    ########################## mcp discovery

    def get_discovered_mcps(self, project_dir: str) -> set:
        try:
            key = self._make_key("discovered_mcps", project_dir)
            data = self._get(key)
            if not data:
                return set()
            return set(data.get("discovered_mcps", []))
        except Exception as e:
            print(f"State error in get_discovered_mcps: {e}")
            return set()

    def add_discovered_mcps(self, project_dir: str, mcps: list):
        try:
            existing = self.get_discovered_mcps(project_dir)
            existing.update(mcps)
            data = {"discovered_mcps": list(existing)}
            key = self._make_key("discovered_mcps", project_dir)
            self._set_with_ttl(key, data, 3600)
        except Exception as e:
            print(f"State error in add_discovered_mcps: {e}")

    def clear_discovered_mcps(self, project_dir: str):
        try:
            key = self._make_key("discovered_mcps", project_dir)
            self._delete(key)
        except Exception as e:
            print(f"State error in clear_discovered_mcps: {e}")

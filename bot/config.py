"""
Per-server configuration management for earthquake-bot.
Stores role IDs, channel IDs, and earthquake notification settings.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration storage location
CONFIG_DIR = Path("configs")
CONFIG_DIR.mkdir(exist_ok=True)


class ServerConfig:
    """Manages per-server configuration."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.config_path = CONFIG_DIR / f"guild_{guild_id}.json"
        self.data = self._load()

    def _load(self) -> dict:
        """Load configuration from disk."""
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.debug(f"✅ Loaded config for guild {self.guild_id}")
                    return data
        except Exception as exc:
            logger.warning(f"⚠️  Failed to load config for guild {self.guild_id}: {exc}")
        return self._default_config()

    def _default_config(self) -> dict:
        """Return default configuration structure."""
        return {
            "guild_id": self.guild_id,
            "earthquake_channel_id": None,
            "earthquake_role_id": None,
            "update_channel_id": None,
            "log_channel_id": None,
            "earthquake_threshold": 30,  # Magnitude 3 (scale value)
            "tsunami_mentions": True,
        }

    def _save(self) -> None:
        """Save configuration to disk."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ Saved config for guild {self.guild_id}")
        except Exception as exc:
            logger.error(f"❌ Failed to save config for guild {self.guild_id}: {exc}")

    def set_earthquake_channel(self, channel_id: Optional[int]) -> None:
        """Set the earthquake notification channel."""
        self.data["earthquake_channel_id"] = channel_id
        self._save()

    def get_earthquake_channel_id(self) -> Optional[int]:
        """Get the earthquake notification channel ID."""
        return self.data.get("earthquake_channel_id")

    def set_earthquake_role(self, role_id: Optional[int]) -> None:
        """Set the earthquake notification role."""
        self.data["earthquake_role_id"] = role_id
        self._save()

    def get_earthquake_role_id(self) -> Optional[int]:
        """Get the earthquake notification role ID."""
        return self.data.get("earthquake_role_id")

    def set_update_channel(self, channel_id: Optional[int]) -> None:
        """Set the update/announcement channel."""
        self.data["update_channel_id"] = channel_id
        self._save()

    def get_update_channel_id(self) -> Optional[int]:
        """Get the update/announcement channel ID."""
        return self.data.get("update_channel_id")

    def set_log_channel(self, channel_id: Optional[int]) -> None:
        """Set the log channel."""
        self.data["log_channel_id"] = channel_id
        self._save()

    def get_log_channel_id(self) -> Optional[int]:
        """Get the log channel ID."""
        return self.data.get("log_channel_id")

    def set_earthquake_threshold(self, threshold: int) -> None:
        """Set the earthquake magnitude threshold."""
        self.data["earthquake_threshold"] = threshold
        self._save()

    def get_earthquake_threshold(self) -> int:
        """Get the earthquake magnitude threshold."""
        return self.data.get("earthquake_threshold", 30)

    def set_tsunami_mentions(self, enabled: bool) -> None:
        """Set whether to mention for tsunami alerts."""
        self.data["tsunami_mentions"] = enabled
        self._save()

    def is_tsunami_mentions_enabled(self) -> bool:
        """Check if tsunami mentions are enabled."""
        return self.data.get("tsunami_mentions", True)

    def is_configured(self) -> bool:
        """Check if the server has been configured."""
        return self.data.get("earthquake_channel_id") is not None

    def get_all(self) -> dict:
        """Get all configuration data."""
        return self.data.copy()


def get_server_config(guild_id: int) -> ServerConfig:
    """Get or create configuration for a guild."""
    return ServerConfig(guild_id)


def has_configured_servers() -> bool:
    """Check if any servers have been configured."""
    try:
        config_files = list(CONFIG_DIR.glob("guild_*.json"))
        return len(config_files) > 0
    except Exception as exc:
        logger.error(f"❌ Error checking configured servers: {exc}")
        return False


def get_all_configured_guilds() -> list[int]:
    """Get list of all configured guild IDs."""
    try:
        guild_ids = []
        for config_file in CONFIG_DIR.glob("guild_*.json"):
            try:
                guild_id = int(config_file.stem.replace("guild_", ""))
                guild_ids.append(guild_id)
            except (ValueError, AttributeError):
                continue
        return guild_ids
    except Exception as exc:
        logger.error(f"❌ Error getting configured guilds: {exc}")
        return []

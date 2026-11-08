"""
Configuration management for the Glottography to Commons pipeline.
Supports loading from environment variables, .env file, or direct instantiation.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Simple parser to load a .env file into os.environ without third-party dependencies."""
    if env_path is None:
        env_path = Path(".env")
    if not env_path.exists():
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


@dataclass
class Config:
    """Pipeline configuration parameters."""

    # Zenodo settings
    zenodo_community: str = "glottography"
    zenodo_api_base: str = "https://zenodo.org/api"
    zenodo_token: Optional[str] = None

    # Wikimedia Commons settings
    commons_api_url: str = "https://commons.wikimedia.org/w/api.php"
    commons_username: Optional[str] = None
    commons_bot_password: Optional[str] = None
    user_agent: str = "GlottographyCommonsBot/1.0 (https://github.com/LanguageMaps/GlottographyToCommons; contact: bot@glottography.org)"

    # MediaWiki categorization
    category_name: str = "Map data of languages"
    talk_category: str = "Map data of languages"
    parent_linguistic_category: str = "Linguistic maps by language mapped"
    parent_category: str = "Map data by object"
    parent_sortkey: str = "Languages"

    # Rate limiting & Wikimedia etiquette
    request_delay: float = 2.0  # seconds between uploads
    maxlag: int = 5             # MediaWiki maxlag parameter (seconds)
    max_retries: int = 5        # backoff retries on 429/503/maxlag
    dry_run: bool = False       # If True, simulate uploads without modifying Commons

    # GeoJSON and Map Data processing
    coordinate_precision: int = 5  # ~1.1m precision
    max_map_bytes: int = 2_000_000 # 2 MB safe limit for Commons Map Data

    # File paths
    base_data_dir: Path = field(default_factory=lambda: Path("data"))
    downloads_dir: Path = field(init=False)
    extracted_dir: Path = field(init=False)
    maps_output_dir: Path = field(init=False)
    export_dir: Path = field(init=False)
    state_db_path: Path = field(init=False)

    def __post_init__(self):
        # Resolve derived directory paths
        self.base_data_dir = Path(self.base_data_dir).resolve()
        self.downloads_dir = self.base_data_dir / "downloads"
        self.extracted_dir = self.base_data_dir / "extracted"
        self.maps_output_dir = self.base_data_dir / "generated_maps"
        self.export_dir = self.base_data_dir / "export"
        self.state_db_path = self.base_data_dir / "pipeline_state.db"

        # Ensure directories exist
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        self.maps_output_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None, **overrides) -> "Config":
        """Load configuration from environment variables and optional .env file."""
        load_env_file(env_path)

        def get_bool(key: str, default: bool) -> bool:
            v = os.getenv(key)
            if v is None:
                return default
            return v.lower() in ("1", "true", "yes", "y", "on")

        def get_int(key: str, default: int) -> int:
            v = os.getenv(key)
            if v is None:
                return default
            try:
                return int(v)
            except ValueError:
                return default

        def get_float(key: str, default: float) -> float:
            v = os.getenv(key)
            if v is None:
                return default
            try:
                return float(v)
            except ValueError:
                return default

        config_data = {
            "zenodo_community": os.getenv("ZENODO_COMMUNITY", "glottography"),
            "zenodo_api_base": os.getenv("ZENODO_API_BASE", "https://zenodo.org/api"),
            "zenodo_token": os.getenv("ZENODO_TOKEN"),
            "commons_api_url": os.getenv("COMMONS_API_URL", "https://commons.wikimedia.org/w/api.php"),
            "commons_username": os.getenv("COMMONS_USERNAME") or os.getenv("COMMONS_USER"),
            "commons_bot_password": os.getenv("COMMONS_BOT_PASSWORD") or os.getenv("COMMONS_PASSWORD"),
            "user_agent": os.getenv("USER_AGENT", "GlottographyCommonsBot/1.0 (https://github.com/LanguageMaps/GlottographyToCommons; contact: bot@glottography.org)"),
            "category_name": os.getenv("CATEGORY_NAME", "Map data of languages"),
            "talk_category": os.getenv("TALK_CATEGORY", "Map data of languages"),
            "parent_linguistic_category": os.getenv("PARENT_LINGUISTIC_CATEGORY", "Linguistic maps by language mapped"),
            "parent_category": os.getenv("PARENT_CATEGORY", "Map data by object"),
            "parent_sortkey": os.getenv("PARENT_SORTKEY", "Languages"),
            "request_delay": get_float("REQUEST_DELAY", 2.0),
            "maxlag": get_int("MAXLAG", 5),
            "max_retries": get_int("MAX_RETRIES", 5),
            "dry_run": get_bool("DRY_RUN", False),
            "coordinate_precision": get_int("COORDINATE_PRECISION", 5),
            "base_data_dir": Path(os.getenv("DATA_DIR", "data")),
        }

        # Apply any explicit argument overrides
        config_data.update(overrides)
        return cls(**config_data)


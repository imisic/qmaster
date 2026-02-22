"""Configuration Manager for Quartermaster"""

import logging
import os
from pathlib import Path
from typing import Any, cast

import yaml
from cryptography.fernet import Fernet


class ConfigManager:
    """Manages all configuration for the backup system"""

    def __init__(self, config_dir: str | None = None):
        self.config_dir = Path(config_dir or Path(__file__).parent.parent.parent / "config")
        self.settings_file = self.config_dir / "settings.yaml"
        self.projects_file = self.config_dir / "projects.yaml"
        self.databases_file = self.config_dir / "databases.yaml"

        # Check if config files exist, guide user to setup if not
        self._check_config_exists()

        # Initialize encryption key for passwords
        self._init_encryption()

        # Load configurations
        self.settings = self._load_yaml(self.settings_file)
        self.projects = self._load_yaml(self.projects_file)
        self.databases = self._load_yaml(self.databases_file)

        # Encrypt database passwords on first run
        self._encrypt_passwords()

    def _check_config_exists(self) -> None:
        """Check if config files exist and provide setup guidance if not"""
        if not self.settings_file.exists():
            example = self.config_dir / "settings.yaml.example"
            if example.exists():
                logging.error(
                    "Configuration not found. Run setup first:\n"
                    "  ./setup.sh\n"
                    "\n"
                    "Or copy example configs manually:\n"
                    f"  cp {example} {self.settings_file}\n"
                    f"  cp {self.config_dir / 'projects.yaml.example'} {self.projects_file}\n"
                    f"  cp {self.config_dir / 'databases.yaml.example'} {self.databases_file}"
                )
                raise SystemExit(1)

    def _init_encryption(self) -> None:
        """Initialize encryption for sensitive data"""
        key_file = self.config_dir / ".encryption_key"

        if key_file.exists():
            # Ensure correct permissions on existing key file
            current_mode = os.stat(key_file).st_mode & 0o777
            if current_mode != 0o600:
                os.chmod(key_file, 0o600)
            with open(key_file, "rb") as f:
                self.cipher = Fernet(f.read())
        else:
            # Generate new key with restricted permissions from creation
            key = Fernet.generate_key()
            fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
            self.cipher = Fernet(key)

    def _load_yaml(self, file_path: Path) -> dict[str, Any]:
        """Load YAML configuration file"""
        if not file_path.exists():
            return {}

        with open(file_path) as f:
            return yaml.safe_load(f) or {}

    def _save_yaml(self, data: dict[str, Any], file_path: Path) -> None:
        """Save configuration to YAML file"""
        with open(file_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def _encrypt_passwords(self) -> None:
        """Encrypt database passwords if not already encrypted"""
        modified = False

        for _db_name, db_config in self.databases.get("databases", {}).items():
            password = db_config.get("password", "")

            # Check if password is already encrypted (starts with 'enc:')
            if password and not password.startswith("enc:"):
                encrypted = self.encrypt_value(password)
                db_config["password"] = f"enc:{encrypted}"
                modified = True

        if modified:
            self._save_yaml(self.databases, self.databases_file)

    def encrypt_value(self, value: str) -> str:
        """Encrypt a string value"""
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt_value(self, encrypted: str) -> str:
        """Decrypt an encrypted value"""
        if encrypted.startswith("enc:"):
            encrypted = encrypted[4:]  # Remove 'enc:' prefix
        return self.cipher.decrypt(encrypted.encode()).decode()

    def get_project(self, name: str) -> dict[str, Any] | None:
        """Get project configuration by name"""
        result = self.projects.get("projects", {}).get(name)
        return cast("dict[str, Any] | None", result)

    def get_database(self, name: str) -> dict[str, Any] | None:
        """Get database configuration by name"""
        db_config: dict[str, Any] | None = self.databases.get("databases", {}).get(name)

        if db_config and "password" in db_config:
            # Decrypt password for use
            password = db_config["password"]
            if password.startswith("enc:"):
                db_config = db_config.copy()
                db_config["password"] = self.decrypt_value(password)

        return db_config

    def get_all_projects(self) -> dict[str, Any]:
        """Get all project configurations"""
        return cast("dict[str, Any]", self.projects.get("projects", {}))

    def get_all_databases(self) -> dict[str, Any]:
        """Get all database configurations"""
        databases: dict[str, Any] = self.databases.get("databases", {}).copy()

        # Decrypt passwords
        for _db_name, db_config in databases.items():
            if "password" in db_config:
                password = db_config["password"]
                if password.startswith("enc:"):
                    db_config["password"] = self.decrypt_value(password)

        return databases

    def get_storage_paths(self) -> dict[str, Path | None]:
        """Get storage paths from settings

        Returns:
            Dict with 'local' (always set) and 'sync' (None if not configured)
        """
        storage = self.settings.get("storage", {})
        # Support both new 'secondary_sync' and legacy 'windows_sync' key
        sync_path = storage.get("secondary_sync") or storage.get("windows_sync")
        return {
            "local": Path(storage.get("local_base", str(Path.home() / "backups" / "qm"))),
            "sync": Path(sync_path) if sync_path else None,
        }

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value with optional default

        Args:
            key: Setting key (supports nested keys with dot notation, e.g., 'storage.local_base')
            default: Default value if setting not found

        Returns:
            Setting value or default
        """
        keys = key.split(".")
        value: Any = self.settings

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def get_global_excludes(self) -> list[str]:
        """Get global exclusion patterns from settings

        Returns:
            List of global exclusion patterns (empty list if not configured)
        """
        return cast("list[str]", self.settings.get("global_exclude", []))

    def add_project(self, name: str, config: dict[str, Any]) -> None:
        """Add a new project configuration"""
        if "projects" not in self.projects:
            self.projects["projects"] = {}

        self.projects["projects"][name] = config
        self._save_yaml(self.projects, self.projects_file)

    def add_database(self, name: str, config: dict[str, Any]) -> None:
        """Add a new database configuration"""
        if "databases" not in self.databases:
            self.databases["databases"] = {}

        # Encrypt password if provided
        if "password" in config:
            config["password"] = f"enc:{self.encrypt_value(config['password'])}"

        self.databases["databases"][name] = config
        self._save_yaml(self.databases, self.databases_file)

    def remove_project(self, name: str) -> bool:
        """Remove a project configuration"""
        if name in self.projects.get("projects", {}):
            del self.projects["projects"][name]
            self._save_yaml(self.projects, self.projects_file)
            return True
        return False

    def remove_database(self, name: str) -> bool:
        """Remove a database configuration"""
        if name in self.databases.get("databases", {}):
            del self.databases["databases"][name]
            self._save_yaml(self.databases, self.databases_file)
            return True
        return False

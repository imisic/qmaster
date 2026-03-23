"""Constants for the backup engine."""

# Subprocess timeout constants (seconds)
MYSQLDUMP_TIMEOUT = 3600  # 1 hour for database dumps
MYSQL_RESTORE_TIMEOUT = 7200  # 2 hours for database restores
GIT_BUNDLE_TIMEOUT = 1800  # 30 min for git bundle create
GIT_CLONE_TIMEOUT = 1800  # 30 min for git clone/fetch
GIT_VERIFY_TIMEOUT = 300  # 5 min for git bundle verify

# MySQL connection defaults
DEFAULT_MYSQL_HOST = "localhost"
DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_USER = "root"

# Default retention periods (days) — must match settings.yaml.example
DEFAULT_PROJECT_RETENTION_DAYS = 30
DEFAULT_DATABASE_RETENTION_DAYS = 14

# Compression and logging constants
ESTIMATED_COMPRESSION_RATIO = 0.7  # tar.gz typically achieves 60-80% compression for code
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB max per log file
LOG_BACKUP_COUNT = 5  # Number of rotated log files to keep
MIN_DB_BACKUP_SPACE_MB = 1024  # 1 GB minimum free space for database backups

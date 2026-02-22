"""Apache Error Log Parser and Manager"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from glob import glob
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.config_manager import ConfigManager


MAX_PARSE_LINES = 50000


class ApacheLogParser:
    def __init__(self, log_paths: list[str] | None = None, config: "ConfigManager | None" = None):
        self.config = config
        self.log_paths = log_paths or self._detect_apache_logs()
        self.log_pattern = re.compile(
            r"\[(?P<timestamp>[^\]]+)\]\s*"
            r"\[(?P<module>[^:]+)?:?(?P<severity>[^\]]+)?\]\s*"
            r"\[pid\s+(?P<pid>\d+)(?::tid\s+(?P<tid>\d+))?\]\s*"
            r"(?:\[client\s+(?P<client>[^\]]+)\]\s*)?"
            r"(?P<message>.*)",
            re.MULTILINE | re.DOTALL,
        )

        self.simple_pattern = re.compile(
            r"\[(?P<timestamp>[^\]]+)\]\s*"
            r"\[(?P<severity>[^\]]+)\]\s*"
            r"(?P<message>.*)",
            re.MULTILINE | re.DOTALL,
        )

    def _detect_apache_logs(self) -> list[str]:
        """Detect common Apache error log locations"""
        # Allow config to specify additional or override paths
        configured_paths: list[str] = []
        if self.config:
            configured_paths = self.config.get_setting("apache.log_paths", [])

        common_paths = configured_paths if configured_paths is not None else [
            "/var/log/apache2/error.log",
            "/var/log/httpd/error_log",
            "/usr/local/apache2/logs/error_log",
            "/opt/lampp/logs/error_log",
            "/var/log/apache/error.log",
            "/home/*/logs/apache/error.log",
            "/home/*/public_html/error_log",
            "/var/log/apache2/other_vhosts_access.log",
        ]

        existing_logs = []
        for path_pattern in common_paths:
            if "*" in path_pattern:
                for path in glob(path_pattern):
                    if os.path.exists(path) and os.access(path, os.R_OK):
                        existing_logs.append(path)
            else:
                if os.path.exists(path_pattern) and os.access(path_pattern, os.R_OK):
                    existing_logs.append(path_pattern)

        return existing_logs

    def parse_log_line(self, line: str) -> dict[str, Any] | None:
        """Parse a single log line"""
        line = line.strip()
        if not line:
            return None

        match = self.log_pattern.match(line)
        if not match:
            match = self.simple_pattern.match(line)
            if not match:
                return {"timestamp": datetime.now().isoformat(), "severity": "unknown", "message": line, "raw": line}

        data = match.groupdict()

        try:
            if data.get("timestamp"):
                timestamp_str = data["timestamp"]
                for fmt in [
                    "%a %b %d %H:%M:%S.%f %Y",
                    "%a %b %d %H:%M:%S %Y",
                    "%Y-%m-%d %H:%M:%S",
                    "%d/%b/%Y:%H:%M:%S %z",
                ]:
                    try:
                        data["timestamp"] = datetime.strptime(timestamp_str, fmt).isoformat()
                        break
                    except ValueError:
                        continue
                else:
                    data["timestamp"] = timestamp_str
        except Exception as e:
            logging.debug(f"Could not parse timestamp: {e}")

        data["raw"] = line

        if data.get("severity"):
            data["severity"] = data["severity"].strip(":").lower()
        else:
            data["severity"] = self._guess_severity(line)

        return data

    def _guess_severity(self, line: str) -> str:
        """Guess severity level from log line content"""
        line_lower = line.lower()
        if "error" in line_lower or "fatal" in line_lower:
            return "error"
        elif "warn" in line_lower or "warning" in line_lower:
            return "warn"
        elif "notice" in line_lower:
            return "notice"
        elif "debug" in line_lower:
            return "debug"
        else:
            return "info"

    def read_logs(
        self, log_path: str, lines: int = 100, severity_filter: str | None = None, search_term: str | None = None
    ) -> list[dict[str, Any]]:
        """Read and parse log file"""
        if not os.path.exists(log_path):
            return []

        try:
            if log_path.endswith(".gz"):
                import gzip

                with gzip.open(log_path, "rt", encoding="utf-8", errors="ignore") as f:
                    log_lines = f.readlines()
            else:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    log_lines = f.readlines()

            if lines > 0:
                log_lines = log_lines[-lines:]
            elif len(log_lines) > MAX_PARSE_LINES:
                logging.warning(f"Log file has {len(log_lines)} lines, capping to {MAX_PARSE_LINES}")
                log_lines = log_lines[-MAX_PARSE_LINES:]

            parsed_logs = []
            for line in log_lines:
                parsed = self.parse_log_line(line)
                if parsed:
                    if severity_filter and parsed.get("severity") != severity_filter.lower():
                        continue

                    if search_term and search_term.lower() not in line.lower():
                        continue

                    parsed_logs.append(parsed)

            return parsed_logs

        except Exception as e:
            return [
                {
                    "timestamp": datetime.now().isoformat(),
                    "severity": "error",
                    "message": f"Error reading log file: {e!s}",
                    "raw": str(e),
                }
            ]

    def _fix_log_permissions(self, log_path: str) -> bool:
        """Automatically fix Apache log permissions if possible"""
        try:
            # Try to fix permissions using sudo without password (if configured)
            log_user = self.config.get_setting("apache.log_user", "www-data") if self.config else "www-data"
            log_group = self.config.get_setting("apache.log_group", "adm") if self.config else "adm"
            commands = [
                ["sudo", "-n", "chmod", "640", log_path],
                ["sudo", "-n", "chown", f"{log_user}:{log_group}", log_path],
            ]

            for cmd in commands:
                try:
                    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    continue

            return os.access(log_path, os.W_OK)
        except Exception:
            return False

    def clear_log(self, log_path: str) -> tuple[bool, str]:
        """Clear/truncate log file"""
        try:
            if not os.path.exists(log_path):
                return False, "Log file does not exist"

            # First try without sudo
            if os.access(log_path, os.W_OK):
                with open(log_path, "w"):
                    pass
                return True, "Log file cleared successfully"

            # Try to auto-fix permissions
            if self._fix_log_permissions(log_path) and os.access(log_path, os.W_OK):
                with open(log_path, "w"):
                    pass
                return True, "Log file cleared successfully (permissions auto-fixed)"

            # Try with sudo truncate
            try:
                subprocess.run(
                    ["sudo", "-n", "truncate", "-s", "0", log_path],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return True, "Log file cleared successfully (with sudo)"
            except subprocess.CalledProcessError:
                pass

            # Try with sudo tee
            try:
                subprocess.run(
                    ["sudo", "-n", "tee", log_path], input="", check=True, capture_output=True, text=True, timeout=10
                )
                return True, "Log file cleared successfully (with sudo)"
            except subprocess.CalledProcessError:
                pass

            # Try adding user to adm group suggestion
            import pwd

            username = pwd.getpwuid(os.getuid()).pw_name

            return False, (
                f"Permission denied. The log file is owned by root. "
                f"To fix this, run one of these commands:\n"
                f"1. sudo usermod -a -G adm {username} (then logout/login)\n"
                f"2. sudo chmod 666 {log_path} (less secure)\n"
                f"3. Clear manually: sudo truncate -s 0 {log_path}"
            )

        except Exception as e:
            return False, f"Error clearing log file: {e!s}"

    def get_log_stats(self, log_path: str) -> dict[str, Any]:
        """Get statistics about log file"""
        stats: dict[str, Any] = {
            "exists": False,
            "size": 0,
            "size_mb": 0.0,
            "readable": False,
            "writable": False,
            "line_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "last_modified": None,
        }

        if not os.path.exists(log_path):
            return stats

        stats["exists"] = True
        stats["readable"] = os.access(log_path, os.R_OK)
        stats["writable"] = os.access(log_path, os.W_OK)

        try:
            file_stat = os.stat(log_path)
            stats["size"] = file_stat.st_size
            stats["size_mb"] = file_stat.st_size / (1024 * 1024)
            stats["last_modified"] = datetime.fromtimestamp(file_stat.st_mtime).isoformat()

            if stats["readable"]:
                logs = self.read_logs(log_path, lines=0)
                stats["line_count"] = len(logs)
                stats["error_count"] = sum(1 for log in logs if log.get("severity") == "error")
                stats["warning_count"] = sum(1 for log in logs if log.get("severity") in ["warn", "warning"])

        except Exception as e:
            logging.warning(f"Error reading log stats for {log_path}: {e}")

        return stats

    def tail_log(self, log_path: str, lines: int = 20) -> list[str]:
        """Tail log file for real-time viewing"""
        if not os.path.exists(log_path):
            return []

        try:
            result = subprocess.run(
                ["tail", "-n", str(lines), log_path], capture_output=True, text=True, check=True, timeout=30
            )
            return result.stdout.splitlines()
        except subprocess.CalledProcessError:
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                    return [line.rstrip() for line in all_lines[-lines:]]
            except Exception as e:
                return [f"Error tailing log: {e!s}"]

    def export_logs(
        self, log_path: str, output_format: str = "json", output_file: str | None = None
    ) -> tuple[bool, str]:
        """Export logs in different formats"""
        try:
            logs = self.read_logs(log_path, lines=0)

            if output_file:
                # Sanitize user-provided filename to prevent path traversal
                output_file = os.path.basename(output_file)
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"apache_logs_{timestamp}.{output_format}"

            if output_format == "json":
                with open(output_file, "w") as f:
                    json.dump(logs, f, indent=2, default=str)

            elif output_format == "csv":
                import csv

                if logs:
                    with open(output_file, "w", newline="") as f:
                        fieldnames = logs[0].keys()
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(logs)

            elif output_format == "txt":
                with open(output_file, "w") as f:
                    for log in logs:
                        f.write(
                            f"[{log.get('timestamp', 'N/A')}] "
                            f"[{log.get('severity', 'N/A')}] "
                            f"{log.get('message', log.get('raw', ''))}\n"
                        )

            else:
                return False, f"Unsupported format: {output_format}"

            return True, f"Logs exported to {output_file}"

        except Exception as e:
            return False, f"Error exporting logs: {e!s}"

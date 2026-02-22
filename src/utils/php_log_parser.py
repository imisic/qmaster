"""PHP Error Log Parser with support for various PHP frameworks"""

import json
import os
import re
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any


class PHPLogParser:
    """Parse PHP error logs including framework-specific logs (Laravel, Symfony, etc.)"""

    def __init__(self, project_paths: list[str] | None = None, system_log_paths: list[str] | None = None):
        self.project_paths = project_paths or []
        self._system_log_paths = system_log_paths  # caller-provided override for system log locations
        self.log_locations = self._detect_php_logs()

        # PHP error log patterns
        self.php_error_pattern = re.compile(
            r"\[(?P<timestamp>[^\]]+)\]\s*"
            r"(?P<level>PHP\s+(?:Fatal error|Warning|Notice|Parse error|Deprecated|Strict Standards)):\s*"
            r"(?P<message>.*?)\s+in\s+"
            r"(?P<file>.*?)\s+on\s+line\s+"
            r"(?P<line>\d+)",
            re.MULTILINE | re.DOTALL,
        )

        # Alternative PHP error pattern
        self.php_error_alt_pattern = re.compile(
            r"\[(?P<timestamp>[^\]]+)\]\s*"
            r"(?P<level>PHP\s+(?:Fatal error|Warning|Notice|Parse error|Deprecated)):\s*"
            r"(?P<message>.*?)$",
            re.MULTILINE,
        )

        # Laravel/Monolog pattern
        self.laravel_pattern = re.compile(
            r"\[(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s*"
            r"(?P<environment>\w+)\.(?P<level>\w+):\s*"
            r"(?P<message>.*?)(?:\s+\{.*?\})?$",
            re.MULTILINE,
        )

        # Stack trace pattern
        self.stack_trace_pattern = re.compile(r"^#\d+\s+.*?(?:\n(?!#\d+).*?)*", re.MULTILINE)

        # Exception pattern
        self.exception_pattern = re.compile(
            r"(?P<exception_type>[A-Z]\w*(?:Exception|Error)):\s*"
            r"(?P<message>.*?)\s+in\s+"
            r"(?P<file>.*?):"
            r"(?P<line>\d+)",
            re.MULTILINE | re.DOTALL,
        )

    def _detect_php_logs(self) -> dict[str, list[str]]:
        """Detect PHP log files in common locations"""
        locations: dict[str, list[str]] = {"system": [], "project": [], "framework": []}

        # System PHP logs — caller can override with explicit paths
        system_paths = self._system_log_paths or [
            "/var/log/php*.log",
            "/var/log/php/*.log",
            "/var/log/php-fpm/*.log",
            "/var/log/php/error.log",
            "/usr/local/var/log/php*.log",
            "/opt/lampp/logs/php_error_log",
            "/tmp/php-errors.log",
        ]

        for path_pattern in system_paths:
            for path in glob(path_pattern):
                if os.path.exists(path) and os.access(path, os.R_OK):
                    locations["system"].append(path)

        # Project-specific logs
        if self.project_paths:
            for project_path in self.project_paths:
                project_dir = Path(project_path)

                # Laravel logs
                laravel_logs = list(project_dir.glob("storage/logs/*.log"))
                locations["framework"].extend([str(p) for p in laravel_logs if p.is_file()])

                # Symfony logs
                symfony_logs = list(project_dir.glob("var/log/*.log"))
                locations["framework"].extend([str(p) for p in symfony_logs if p.is_file()])

                # WordPress debug.log
                wp_logs = list(project_dir.glob("wp-content/debug.log"))
                locations["framework"].extend([str(p) for p in wp_logs if p.is_file()])

                # Generic project error logs
                generic_logs = list(project_dir.glob("logs/*.log"))
                generic_logs.extend(list(project_dir.glob("error*.log")))
                locations["project"].extend([str(p) for p in generic_logs if p.is_file()])

        return locations

    def parse_php_error(self, line: str) -> dict[str, Any] | None:
        """Parse a PHP error log line"""
        # Try standard PHP error format
        match = self.php_error_pattern.match(line)
        if match:
            data = match.groupdict()
            return {
                "type": "php_error",
                "timestamp": self._parse_timestamp(data.get("timestamp", "")),
                "level": self._normalize_level(data.get("level", "")),
                "message": data.get("message", "").strip(),
                "file": data.get("file", "").strip(),
                "line": int(data.get("line", 0)),
                "raw": line,
            }

        # Try alternative format
        match = self.php_error_alt_pattern.match(line)
        if match:
            data = match.groupdict()
            return {
                "type": "php_error",
                "timestamp": self._parse_timestamp(data.get("timestamp", "")),
                "level": self._normalize_level(data.get("level", "")),
                "message": data.get("message", "").strip(),
                "raw": line,
            }

        # Try Laravel/Monolog format
        match = self.laravel_pattern.match(line)
        if match:
            data = match.groupdict()
            return {
                "type": "framework",
                "framework": "laravel",
                "timestamp": data.get("timestamp", ""),
                "environment": data.get("environment", ""),
                "level": data.get("level", "").lower(),
                "message": data.get("message", "").strip(),
                "raw": line,
            }

        # Try to detect exceptions
        match = self.exception_pattern.search(line)
        if match:
            data = match.groupdict()
            return {
                "type": "exception",
                "exception_type": data.get("exception_type", ""),
                "message": data.get("message", "").strip(),
                "file": data.get("file", "").strip(),
                "line": int(data.get("line", 0)),
                "level": "error",
                "raw": line,
            }

        return None

    def _normalize_level(self, level: str) -> str:
        """Normalize PHP error level to standard severity"""
        level_lower = level.lower()

        # Check keywords in order of severity
        for keyword, severity in [
            ("fatal", "fatal"),
            ("parse error", "fatal"),
            ("emergency", "emergency"),
            ("alert", "alert"),
            ("critical", "critical"),
            ("error", "error"),
            ("exception", "error"),
            ("warning", "warning"),
            ("notice", "notice"),
            ("deprecated", "deprecated"),
            ("strict", "strict"),
            ("debug", "debug"),
            ("info", "info"),
        ]:
            if keyword in level_lower:
                return severity
        return "info"

    def _parse_timestamp(self, timestamp_str: str) -> str:
        """Parse various timestamp formats"""
        if not timestamp_str:
            return datetime.now().isoformat()

        # Common PHP timestamp formats
        formats = [
            "%d-%b-%Y %H:%M:%S %Z",  # 01-Jan-2024 12:34:56 UTC
            "%d-%b-%Y %H:%M:%S",  # 01-Jan-2024 12:34:56
            "%Y-%m-%d %H:%M:%S",  # 2024-01-01 12:34:56
            "%Y/%m/%d %H:%M:%S",  # 2024/01/01 12:34:56
            "%b %d %H:%M:%S",  # Jan 01 12:34:56
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp_str.strip(), fmt)
                # If year is missing, use current year
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return dt.isoformat()
            except ValueError:
                continue

        return timestamp_str

    def parse_stack_trace(self, lines: list[str], start_index: int) -> dict[str, Any]:
        """Parse a PHP stack trace starting from the given index"""
        trace_lines = []
        trace_frames = []

        i = start_index
        while i < len(lines):
            line = lines[i]

            # Check if this is a stack frame
            frame_match = re.match(r"^#(\d+)\s+(.*?)$", line)
            if frame_match:
                frame_num = int(frame_match.group(1))
                frame_content = frame_match.group(2)

                # Parse frame details
                frame_data = {"number": frame_num, "content": frame_content}

                # Try to extract file and line from frame
                file_match = re.search(r"(.+?):(\d+)$", frame_content)
                if file_match:
                    frame_data["file"] = file_match.group(1)
                    frame_data["line"] = int(file_match.group(2))

                # Try to extract function/method
                func_match = re.search(r"(\w+(?:::\w+)?)\(", frame_content)
                if func_match:
                    frame_data["function"] = func_match.group(1)

                trace_frames.append(frame_data)
                trace_lines.append(line)
            elif line.strip() and not line.startswith("["):
                # Continuation of stack trace
                trace_lines.append(line)
            else:
                # End of stack trace
                break

            i += 1

        return {"frames": trace_frames, "raw_trace": "\n".join(trace_lines), "frame_count": len(trace_frames)}

    def read_php_logs(
        self,
        log_path: str,
        lines: int = 100,
        level_filter: str | None = None,
        search_term: str | None = None,
        parse_stack_traces: bool = True,
    ) -> list[dict[str, Any]]:
        """Read and parse PHP log file"""
        if not os.path.exists(log_path):
            return []

        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                log_lines = f.readlines()

            if lines > 0:
                log_lines = log_lines[-lines:]

            parsed_logs = []
            i = 0

            while i < len(log_lines):
                line = log_lines[i].strip()

                if not line:
                    i += 1
                    continue

                # Parse the line
                parsed = self.parse_php_error(line)

                if parsed:
                    # Check for stack trace following the error
                    if parse_stack_traces and i + 1 < len(log_lines):
                        next_line = log_lines[i + 1].strip()
                        if next_line.startswith("#0"):
                            trace = self.parse_stack_trace(log_lines, i + 1)
                            parsed["stack_trace"] = trace
                            i += len(trace["raw_trace"].split("\n"))

                    # Apply filters
                    if level_filter and parsed.get("level") != level_filter.lower():
                        i += 1
                        continue

                    if search_term and search_term.lower() not in line.lower():
                        i += 1
                        continue

                    parsed_logs.append(parsed)

                i += 1

            return parsed_logs

        except Exception as e:
            return [
                {
                    "type": "error",
                    "timestamp": datetime.now().isoformat(),
                    "level": "error",
                    "message": f"Error reading PHP log file: {e!s}",
                    "raw": str(e),
                }
            ]

    def get_error_summary(self, log_path: str, last_hours: int = 24) -> dict[str, Any]:
        """Get summary of PHP errors in the last N hours"""
        from datetime import timedelta

        cutoff_time = datetime.now() - timedelta(hours=last_hours)
        logs = self.read_php_logs(log_path, lines=0)

        summary = {
            "total_errors": 0,
            "fatal_errors": 0,
            "warnings": 0,
            "notices": 0,
            "deprecated": 0,
            "exceptions": 0,
            "by_file": {},
            "by_type": {},
            "recent_fatal": [],
            "most_common": [],
        }

        error_counts: dict[str, int] = {}
        total_errors = 0
        fatal_errors = 0
        warnings = 0
        notices = 0
        deprecated = 0
        exceptions = 0
        by_file: dict[str, int] = {}
        by_type: dict[str, int] = {}
        recent_fatal: list[dict[str, Any]] = []

        for log in logs:
            # Parse timestamp and check if within time range
            try:
                log_time = datetime.fromisoformat(log.get("timestamp", ""))
                if log_time < cutoff_time:
                    continue
            except (ValueError, TypeError):
                pass

            total_errors += 1

            # Count by level
            level = log.get("level", "unknown")
            if level == "fatal":
                fatal_errors += 1
                recent_fatal.append(log)
            elif level == "warning":
                warnings += 1
            elif level == "notice":
                notices += 1
            elif level == "deprecated":
                deprecated += 1

            # Count by type
            if log.get("type") == "exception":
                exceptions += 1
                exc_type = log.get("exception_type", "Unknown")
                by_type[exc_type] = by_type.get(exc_type, 0) + 1

            # Count by file
            if "file" in log:
                file_path = log["file"]
                by_file[file_path] = by_file.get(file_path, 0) + 1

            # Track most common errors
            error_key = f"{level}:{log.get('message', '')[:100]}"
            error_counts[error_key] = error_counts.get(error_key, 0) + 1

        # Get top 5 most common errors
        most_common: list[dict[str, Any]] = []
        if error_counts:
            sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)
            most_common = [{"error": k, "count": v} for k, v in sorted_errors[:5]]

        # Limit recent fatal errors to last 10
        recent_fatal = recent_fatal[-10:]

        summary["total_errors"] = total_errors
        summary["fatal_errors"] = fatal_errors
        summary["warnings"] = warnings
        summary["notices"] = notices
        summary["deprecated"] = deprecated
        summary["exceptions"] = exceptions
        summary["by_file"] = by_file
        summary["by_type"] = by_type
        summary["recent_fatal"] = recent_fatal
        summary["most_common"] = most_common

        return summary

    def find_project_logs(self, project_path: str) -> dict[str, list[str]]:
        """Find all PHP-related logs in a project directory"""
        project_dir = Path(project_path)
        found_logs: dict[str, list[str]] = {"php_errors": [], "framework": [], "application": [], "debug": []}

        if not project_dir.exists():
            return found_logs

        # Common PHP error log names
        error_patterns = ["*error*.log", "php*.log", "*exception*.log", "*fatal*.log"]
        for pattern in error_patterns:
            found_logs["php_errors"].extend(
                [
                    str(p)
                    for p in project_dir.rglob(pattern)
                    if p.is_file() and "vendor" not in str(p) and "node_modules" not in str(p)
                ]
            )

        # Laravel logs
        laravel_logs = project_dir / "storage" / "logs"
        if laravel_logs.exists():
            found_logs["framework"].extend([str(p) for p in laravel_logs.glob("*.log")])

        # Symfony logs
        symfony_logs = project_dir / "var" / "log"
        if symfony_logs.exists():
            found_logs["framework"].extend([str(p) for p in symfony_logs.glob("*.log")])

        # WordPress debug
        wp_debug = project_dir / "wp-content" / "debug.log"
        if wp_debug.exists():
            found_logs["debug"].append(str(wp_debug))

        # Generic application logs
        app_log_dirs = ["logs", "log", "var/log", "tmp/logs"]
        for log_dir in app_log_dirs:
            log_path = project_dir / log_dir
            if log_path.exists() and log_path.is_dir():
                found_logs["application"].extend([str(p) for p in log_path.glob("*.log")])

        # Remove duplicates
        for category in found_logs:
            found_logs[category] = list(set(found_logs[category]))

        return found_logs

    def export_error_report(
        self, log_paths: list[str], output_file: str | None = None, output_format: str = "html", last_hours: int = 24
    ) -> tuple[bool, str]:
        """Generate an error report from multiple log files"""
        try:
            if not output_file:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"php_error_report_{timestamp}.{output_format}"

            all_errors = []
            summaries = {}

            for log_path in log_paths:
                if os.path.exists(log_path):
                    errors = self.read_php_logs(log_path, lines=0)
                    all_errors.extend(errors)
                    summaries[log_path] = self.get_error_summary(log_path, last_hours)

            if output_format == "json":
                report = {
                    "generated": datetime.now().isoformat(),
                    "log_files": log_paths,
                    "summaries": summaries,
                    "all_errors": all_errors,
                }
                with open(output_file, "w") as f:
                    json.dump(report, f, indent=2, default=str)

            elif output_format == "html":
                html_content = self._generate_html_report(summaries, all_errors, last_hours)
                with open(output_file, "w") as f:
                    f.write(html_content)

            elif output_format == "txt":
                with open(output_file, "w") as f:
                    f.write(f"PHP Error Report - Generated: {datetime.now()}\n")
                    f.write("=" * 80 + "\n\n")

                    for log_path, summary in summaries.items():
                        f.write(f"\nLog File: {log_path}\n")
                        f.write("-" * 40 + "\n")
                        f.write(f"Total Errors: {summary['total_errors']}\n")
                        f.write(f"Fatal Errors: {summary['fatal_errors']}\n")
                        f.write(f"Warnings: {summary['warnings']}\n")
                        f.write(f"Notices: {summary['notices']}\n\n")

            return True, f"Error report generated: {output_file}"

        except Exception as e:
            return False, f"Failed to generate report: {e!s}"

    def _generate_html_report(self, summaries: dict[str, dict[str, Any]], errors: list[dict[str, Any]], hours: int) -> str:
        """Generate HTML error report"""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>PHP Error Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .header {{ background: #dc3545; color: white; padding: 20px; border-radius: 5px; }}
        .summary {{ background: white; padding: 20px; margin: 20px 0; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .error {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0; }}
        .fatal {{ background: #f8d7da; border-left: 4px solid #dc3545; }}
        .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; }}
        .notice {{ background: #d1ecf1; border-left: 4px solid #17a2b8; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f8f9fa; }}
        .file-path {{ font-family: monospace; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>PHP Error Report</h1>
        <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <p>Time Range: Last {hours} hours</p>
    </div>
"""

        # Add summaries
        for log_path, summary in summaries.items():
            html += f"""
    <div class="summary">
        <h2>Log: {log_path}</h2>
        <table>
            <tr>
                <th>Total Errors</th>
                <th>Fatal</th>
                <th>Warnings</th>
                <th>Notices</th>
                <th>Exceptions</th>
            </tr>
            <tr>
                <td>{summary["total_errors"]}</td>
                <td style="color: #dc3545; font-weight: bold;">{summary["fatal_errors"]}</td>
                <td style="color: #ffc107;">{summary["warnings"]}</td>
                <td style="color: #17a2b8;">{summary["notices"]}</td>
                <td>{summary["exceptions"]}</td>
            </tr>
        </table>

        <h3>Most Common Errors</h3>
        <ul>
"""
            for error_info in summary.get("most_common", [])[:5]:
                html += f"<li>{error_info['error']} ({error_info['count']} occurrences)</li>"

            html += "</ul></div>"

        # Add recent fatal errors
        html += """
    <div class="summary">
        <h2>Recent Fatal Errors</h2>
"""

        for summary in summaries.values():
            for error in summary.get("recent_fatal", [])[:5]:
                level_class = error.get("level", "error").replace(" ", "-")
                html += f"""
        <div class="error {level_class}">
            <strong>[{error.get("timestamp", "N/A")}] {error.get("level", "ERROR").upper()}</strong><br>
            <strong>Message:</strong> {error.get("message", "No message")}<br>
            <span class="file-path">File: {error.get("file", "Unknown")} Line: {error.get("line", "?")}</span>
        </div>
"""

        html += """
    </div>
</body>
</html>
"""
        return html

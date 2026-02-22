"""Automated backup scheduler using cron"""

import logging
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path


class BackupScheduler:
    """Manage automated backup schedules using cron"""

    def __init__(self):
        self.script_path = Path(__file__).parent.parent.parent / "qm.sh"
        self.python_cli_path = Path(__file__).parent.parent / "cli.py"

    def get_current_crontab(self) -> list[str]:
        """Get current user's crontab entries"""
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.strip().split("\n") if result.stdout else []
            else:
                return []
        except Exception as e:
            logging.warning(f"Failed to read crontab: {e}")
            return []

    def set_crontab(self, entries: list[str]) -> tuple[bool, str]:
        """Set the user's crontab to the given entries"""
        try:
            # Create temporary file with new crontab
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".cron") as f:
                f.write("\n".join(entries) + "\n")
                temp_file = f.name

            # Install new crontab
            result = subprocess.run(["crontab", temp_file], capture_output=True, text=True, timeout=30)

            # Clean up temp file
            os.unlink(temp_file)

            if result.returncode == 0:
                return True, "Crontab updated successfully"
            else:
                return False, f"Failed to update crontab: {result.stderr}"

        except Exception as e:
            return False, f"Error updating crontab: {e!s}"

    def add_backup_schedule(self, schedule: str, command: str, comment: str | None = None) -> tuple[bool, str]:
        """Add a backup schedule to crontab

        Args:
            schedule: Cron schedule expression (e.g., '0 2 * * *' for daily at 2 AM)
            command: Backup command to execute
            comment: Optional comment for the cron entry

        Returns:
            Tuple of (success, message)
        """
        # Get current crontab
        entries = self.get_current_crontab()

        # Check if command already exists
        for entry in entries:
            if command in entry and not entry.strip().startswith("#"):
                return False, "This backup schedule already exists"

        # Add comment if provided
        if comment:
            entries.append(f"# {comment}")

        # Add the cron entry
        entries.append(f"{schedule} {command}")

        # Update crontab
        return self.set_crontab(entries)

    def remove_backup_schedule(self, pattern: str) -> tuple[bool, str]:
        """Remove backup schedules matching pattern

        Args:
            pattern: Pattern to match in cron entries

        Returns:
            Tuple of (success, message)
        """
        entries = self.get_current_crontab()
        original_count = len(entries)

        # Filter out matching entries and their comments
        filtered = []
        skip_next_comment = False
        for i, entry in enumerate(entries):
            # Check if this is a comment for the next line
            if entry.strip().startswith("#") and i + 1 < len(entries):
                next_entry = entries[i + 1]
                if pattern in next_entry:
                    skip_next_comment = True
                    continue

            if skip_next_comment:
                skip_next_comment = False
                continue

            if pattern not in entry:
                filtered.append(entry)

        removed_count = original_count - len(filtered)

        if removed_count == 0:
            return False, f"No schedules found matching pattern: {pattern}"

        success, msg = self.set_crontab(filtered)
        if success:
            return True, f"Removed {removed_count} schedule(s)"
        else:
            return False, msg

    def list_backup_schedules(self) -> list[dict[str, str]]:
        """List all backup-related cron schedules

        Returns:
            List of schedule dictionaries
        """
        entries = self.get_current_crontab()
        schedules = []

        # Pattern to match cron schedule
        cron_pattern = re.compile(r"^([\d\*\/\-,]+\s+){5}(.+)$")

        for i, entry in enumerate(entries):
            entry = entry.strip()

            # Skip empty lines and comments
            if not entry or entry.startswith("#"):
                continue

            # Check if this is a backup-related entry
            if "qm" in entry or "backup" in entry.lower() or "cli.py" in entry:
                match = cron_pattern.match(entry)
                if match:
                    # Parse the schedule
                    parts = entry.split(None, 5)
                    if len(parts) >= 6:
                        schedule = " ".join(parts[:5])
                        command = parts[5]

                        # Look for comment above this entry
                        comment = None
                        if i > 0 and entries[i - 1].strip().startswith("#"):
                            comment = entries[i - 1].strip()[1:].strip()

                        schedules.append(
                            {
                                "schedule": schedule,
                                "command": command,
                                "comment": comment or "No description",
                                "human_readable": self.parse_cron_schedule(schedule),
                            }
                        )

        return schedules

    def parse_cron_schedule(self, schedule: str) -> str:
        """Convert cron schedule to human-readable format

        Args:
            schedule: Cron schedule expression

        Returns:
            Human-readable description
        """
        parts = schedule.split()
        if len(parts) != 5:
            return "Invalid schedule"

        minute, hour, day, month, weekday = parts

        # Common patterns
        if schedule == "0 0 * * *":
            return "Daily at midnight"
        elif schedule == "0 2 * * *":
            return "Daily at 2:00 AM"
        elif schedule == "0 3 * * 0":
            return "Weekly on Sunday at 3:00 AM"
        elif schedule == "0 4 1 * *":
            return "Monthly on 1st at 4:00 AM"
        elif schedule == "*/30 * * * *":
            return "Every 30 minutes"
        elif schedule == "0 */6 * * *":
            return "Every 6 hours"
        elif minute == "0" and hour != "*":
            return f"Daily at {hour}:00"
        elif minute != "*" and hour != "*":
            return f"Daily at {hour}:{minute.zfill(2)}"
        else:
            # Build description from parts
            desc = []

            if minute == "*":
                desc.append("Every minute")
            elif "/" in minute:
                desc.append(f"Every {minute.split('/')[1]} minutes")
            else:
                desc.append(f"At minute {minute}")

            if hour != "*":
                if "/" in hour:
                    desc.append(f"every {hour.split('/')[1]} hours")
                else:
                    desc.append(f"at hour {hour}")

            if day != "*":
                desc.append(f"on day {day}")

            if month != "*":
                desc.append(f"in month {month}")

            if weekday != "*":
                days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                if weekday.isdigit() and 0 <= int(weekday) <= 6:
                    desc.append(f"on {days[int(weekday)]}")

            return ", ".join(desc)

    def create_schedule_templates(self) -> dict[str, dict[str, str]]:
        """Get common schedule templates

        Returns:
            Dictionary of schedule templates
        """
        return {
            "hourly": {"schedule": "0 * * * *", "description": "Every hour at minute 0"},
            "daily": {"schedule": "0 2 * * *", "description": "Daily at 2:00 AM"},
            "daily_noon": {"schedule": "0 12 * * *", "description": "Daily at noon"},
            "twice_daily": {"schedule": "0 2,14 * * *", "description": "Twice daily at 2:00 AM and 2:00 PM"},
            "weekly": {"schedule": "0 3 * * 0", "description": "Weekly on Sunday at 3:00 AM"},
            "monthly": {"schedule": "0 4 1 * *", "description": "Monthly on 1st at 4:00 AM"},
            "every_30min": {"schedule": "*/30 * * * *", "description": "Every 30 minutes"},
            "every_6hours": {"schedule": "0 */6 * * *", "description": "Every 6 hours"},
            "weekdays": {"schedule": "0 1 * * 1-5", "description": "Weekdays (Mon-Fri) at 1:00 AM"},
            "weekends": {"schedule": "0 3 * * 0,6", "description": "Weekends (Sat-Sun) at 3:00 AM"},
        }

    def generate_backup_command(self, backup_type: str, target: str, use_wrapper: bool = True) -> str:
        """Generate backup command for cron

        Args:
            backup_type: 'project', 'database', or 'snapshot'
            target: Project or database name
            use_wrapper: Use shell wrapper script if available

        Returns:
            Command string for cron
        """
        # Determine which script to use
        if use_wrapper and self.script_path.exists():
            base_cmd = str(self.script_path)
        else:
            # Use Python directly
            python_path = subprocess.run(
                ["which", "python3"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
            if not python_path:
                python_path = "python3"
            base_cmd = f"{python_path} {self.python_cli_path}"

        # Sanitize target to prevent shell injection in cron entries
        safe_target = shlex.quote(target) if target else ""

        # Build command based on type
        match backup_type:
            case "project":
                return f"{base_cmd} backup --project {safe_target} > /dev/null 2>&1"
            case "database":
                return f"{base_cmd} backup-db --database {safe_target} > /dev/null 2>&1"
            case "snapshot":
                return f"{base_cmd} snapshot {safe_target} > /dev/null 2>&1"
            case "all-projects":
                return f"{base_cmd} backup --all > /dev/null 2>&1"
            case "all-databases":
                return f"{base_cmd} backup-db --all > /dev/null 2>&1"
            case _:
                raise ValueError(f"Invalid backup type: {backup_type}")

    def setup_default_schedules(
        self, projects: list[str], databases: list[str], important_projects: list[str] | None = None
    ) -> tuple[bool, str]:
        """Setup default backup schedules for all projects and databases

        Args:
            projects: List of project names
            databases: List of database names
            important_projects: Optional list of project names for weekly snapshots

        Returns:
            Tuple of (success, message)
        """
        added = 0
        errors = []

        # Add daily backup for all projects at 2 AM
        cmd = self.generate_backup_command("all-projects", "")
        success, msg = self.add_backup_schedule("0 2 * * *", cmd, "Daily backup of all projects")
        if success:
            added += 1
        elif "already exists" not in msg:
            errors.append(msg)

        # Add daily backup for all databases at 3 AM
        cmd = self.generate_backup_command("all-databases", "")
        success, msg = self.add_backup_schedule("0 3 * * *", cmd, "Daily backup of all databases")
        if success:
            added += 1
        elif "already exists" not in msg:
            errors.append(msg)

        # Add weekly snapshot for important projects (Sunday at 4 AM)
        for project in important_projects or []:
            if project in projects:
                cmd = self.generate_backup_command("snapshot", project)
                success, msg = self.add_backup_schedule("0 4 * * 0", cmd, f"Weekly snapshot of {project}")
                if success:
                    added += 1

        if errors:
            return False, f"Added {added} schedules with errors: {'; '.join(errors)}"
        else:
            return True, f"Successfully added {added} default backup schedules"

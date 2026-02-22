"""Desktop Notification Manager for Quartermaster"""

import logging
import subprocess


class NotificationManager:
    """Manages desktop notifications for backup operations"""

    def __init__(self):
        self.logger = logging.getLogger("NotificationManager")
        self.enabled = self._check_notification_support()

    def _check_notification_support(self) -> bool:
        """Check if desktop notifications are supported"""
        try:
            # Check if notify-send is available (Linux/WSL)
            result = subprocess.run(["which", "notify-send"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                self.logger.debug("Desktop notifications enabled (notify-send)")
                return True
        except Exception as e:
            self.logger.debug(f"Notification check failed: {e}")

        self.logger.debug("Desktop notifications not available")
        return False

    def send(self, title: str, message: str, urgency: str = "normal", icon: str | None = None) -> bool:
        """Send a desktop notification

        Args:
            title: Notification title
            message: Notification message
            urgency: Urgency level ('low', 'normal', 'critical')
            icon: Optional icon name

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            cmd = ["notify-send", f"--urgency={urgency}"]

            if icon:
                cmd.extend(["--icon", icon])

            cmd.extend([title, message])

            subprocess.run(cmd, check=False, capture_output=True, timeout=10)
            return True

        except Exception as e:
            self.logger.warning(f"Failed to send notification: {e}")
            return False

    def notify_backup_success(self, item_name: str, item_type: str = "project", size_mb: float = 0) -> bool:
        """Send notification for successful backup

        Args:
            item_name: Name of the backed up item
            item_type: Type of item ('project' or 'database')
            size_mb: Size of backup in MB

        Returns:
            True if notification sent successfully
        """
        title = "✅ Backup Successful"
        message = f"{item_type.capitalize()}: {item_name}"
        if size_mb > 0:
            message += f"\nSize: {size_mb:.2f} MB"

        return self.send(title, message, urgency="normal", icon="emblem-default")

    def notify_backup_failure(self, item_name: str, item_type: str = "project", error: str = "") -> bool:
        """Send notification for failed backup

        Args:
            item_name: Name of the item that failed to backup
            item_type: Type of item ('project' or 'database')
            error: Error message

        Returns:
            True if notification sent successfully
        """
        title = "❌ Backup Failed"
        message = f"{item_type.capitalize()}: {item_name}"
        if error:
            # Truncate long error messages
            error_short = error[:100] + "..." if len(error) > 100 else error
            message += f"\nError: {error_short}"

        return self.send(title, message, urgency="critical", icon="dialog-error")

    def notify_snapshot_complete(self, project_name: str, success_count: int, total_count: int) -> bool:
        """Send notification for completed snapshot

        Args:
            project_name: Name of the project
            success_count: Number of successful operations
            total_count: Total number of operations

        Returns:
            True if notification sent successfully
        """
        if success_count == total_count:
            title = "✅ Snapshot Complete"
            icon = "emblem-default"
            urgency = "normal"
        else:
            title = "⚠️ Snapshot Partially Complete"
            icon = "dialog-warning"
            urgency = "normal"

        message = f"Project: {project_name}\n{success_count}/{total_count} operations successful"

        return self.send(title, message, urgency=urgency, icon=icon)


"""
Claude Config Manager — MCP server configuration management.
"""

import json
import logging
import shutil

logger = logging.getLogger(__name__)


class _MCPMixin:
    """MCP server CRUD operations. Requires _ClaudeConfigBase attributes."""

    def get_mcp_servers(self) -> tuple[bool, list[dict], str]:
        """
        Get list of configured MCP servers

        Returns:
            Tuple of (success, servers_list, error_message)
        """
        if not self.mcp_config_path.exists():
            return True, [], ""

        try:
            with open(self.mcp_config_path) as f:
                config = json.load(f)

            servers = []
            mcp_servers = config.get("mcpServers", {})

            for name, settings in mcp_servers.items():
                servers.append(
                    {
                        "name": name,
                        "command": settings.get("command", ""),
                        "args": settings.get("args", []),
                        "env": settings.get("env", {}),
                        "disabled": settings.get("disabled", False),
                    }
                )

            return True, servers, ""

        except json.JSONDecodeError as e:
            return False, [], f"Invalid JSON: {e}"
        except Exception as e:
            return False, [], f"Error reading MCP config: {e}"

    def save_mcp_servers(self, servers: list[dict]) -> tuple[bool, str]:
        """
        Save MCP servers configuration

        Args:
            servers: List of server dictionaries

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Read existing config or create new
            if self.mcp_config_path.exists():
                with open(self.mcp_config_path) as f:
                    config = json.load(f)
            else:
                config = {}

            # Rebuild mcpServers section
            mcp_servers = {}
            for server in servers:
                server_config = {
                    "command": server["command"],
                }
                if server.get("args"):
                    server_config["args"] = server["args"]
                if server.get("env"):
                    server_config["env"] = server["env"]
                if server.get("disabled"):
                    server_config["disabled"] = True

                mcp_servers[server["name"]] = server_config

            config["mcpServers"] = mcp_servers

            # Create backup
            if self.mcp_config_path.exists():
                backup_path = self.mcp_config_path.with_suffix(".json.backup")
                shutil.copy2(self.mcp_config_path, backup_path)

            # Write updated config
            with open(self.mcp_config_path, "w") as f:
                json.dump(config, f, indent=2)

            return True, ""

        except Exception as e:
            logger.error(f"Error saving MCP config: {e}")
            return False, str(e)

    def add_mcp_server(
        self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None
    ) -> tuple[bool, str]:
        """
        Add a new MCP server

        Args:
            name: Server name
            command: Command to run
            args: Command arguments
            env: Environment variables

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Check if name already exists
        if any(s["name"] == name for s in servers):
            return False, f"Server '{name}' already exists"

        # Add new server
        new_server = {"name": name, "command": command, "args": args or [], "env": env or {}, "disabled": False}

        servers.append(new_server)
        return self.save_mcp_servers(servers)

    def delete_mcp_server(self, name: str) -> tuple[bool, str]:
        """
        Delete an MCP server

        Args:
            name: Server name to delete

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Filter out the server
        servers = [s for s in servers if s["name"] != name]
        return self.save_mcp_servers(servers)

    def update_mcp_server(self, old_name: str, updated_server: dict) -> tuple[bool, str]:
        """
        Update an existing MCP server

        Args:
            old_name: Current server name
            updated_server: Updated server dictionary

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Find and update the server
        found = False
        for i, server in enumerate(servers):
            if server["name"] == old_name:
                servers[i] = updated_server
                found = True
                break

        if not found:
            return False, f"Server '{old_name}' not found"

        return self.save_mcp_servers(servers)

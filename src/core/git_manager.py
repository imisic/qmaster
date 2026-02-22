"""Git Integration Manager for Quartermaster"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from git import InvalidGitRepositoryError, Repo


class GitManager:
    """Manages Git operations for projects"""

    def __init__(self):
        self.logger = logging.getLogger("GitManager")

    def is_git_repo(self, path: str) -> bool:
        """Check if path is a Git repository"""
        try:
            Repo(path)
            return True
        except InvalidGitRepositoryError:
            return False

    def get_repo_status(self, path: str) -> dict[str, Any]:
        """Get Git repository status"""
        if not self.is_git_repo(path):
            return {"is_repo": False, "error": "Not a Git repository"}

        try:
            repo = Repo(path)

            # Get current branch
            try:
                current_branch = repo.active_branch.name
            except TypeError:
                current_branch = "detached HEAD"

            # Get uncommitted changes
            changed_files = []
            if repo.is_dirty():
                changed_files = [item.a_path for item in repo.index.diff(None)]

            # Get untracked files
            untracked_files = repo.untracked_files

            # Get recent commits
            commits = []
            for commit in list(repo.iter_commits(max_count=10)):
                commits.append(
                    {
                        "hash": commit.hexsha[:7],
                        "message": commit.message.strip(),
                        "author": str(commit.author),
                        "date": datetime.fromtimestamp(commit.committed_date).isoformat(),
                        "is_savepoint": "savepoint" in commit.message.lower(),
                    }
                )

            # Get remotes
            remotes = []
            for remote in repo.remotes:
                remotes.append({"name": remote.name, "url": next(iter(remote.urls)) if remote.urls else None})

            return {
                "is_repo": True,
                "branch": current_branch,
                "is_dirty": repo.is_dirty(),
                "changed_files": changed_files,
                "untracked_files": untracked_files,
                "total_changes": len(changed_files) + len(untracked_files),
                "commits": commits,
                "commit_count": int(repo.git.rev_list("--count", "HEAD")),
                "remotes": remotes,
                "has_remote": len(remotes) > 0,
            }

        except Exception as e:
            self.logger.error(f"Failed to get repo status for {path}: {e!s}")
            return {"is_repo": True, "error": str(e)}

    def create_savepoint(self, path: str, message: str | None = None) -> tuple[bool, str]:
        """Create a Git savepoint (commit all changes)"""
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Check if there are changes to commit
            if not repo.is_dirty() and not repo.untracked_files:
                return False, "No changes to commit"

            # Configure Git safe directory (safe from shell injection)
            try:
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", str(path)],
                    check=False,  # Don't raise exception if already configured
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception as e:
                self.logger.warning(f"Failed to configure safe.directory: {e}")

            # Add all changes
            repo.git.add(A=True)

            # Create commit message
            if not message:
                message = f"Savepoint - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            # Commit
            commit = repo.index.commit(message)

            self.logger.info(f"Created savepoint for {path}: {commit.hexsha[:7]}")

            return True, f"Savepoint created: {commit.hexsha[:7]} - {message}"

        except Exception as e:
            self.logger.error(f"Failed to create savepoint for {path}: {e!s}")
            return False, f"Failed to create savepoint: {e!s}"

    def quick_commit(self, path: str, message: str | None = None) -> tuple[bool, str]:
        """Quick commit (same as savepoint but with different default message)"""
        if not message:
            message = f"Quick save - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.create_savepoint(path, message)

    def get_commit_history(self, path: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get commit history for a repository"""
        if not self.is_git_repo(path):
            return []

        try:
            repo = Repo(path)
            commits = []

            for commit in list(repo.iter_commits(max_count=limit)):
                commits.append(
                    {
                        "hash": commit.hexsha,
                        "short_hash": commit.hexsha[:7],
                        "message": commit.message.strip(),
                        "author": str(commit.author),
                        "author_email": commit.author.email,
                        "date": datetime.fromtimestamp(commit.committed_date).isoformat(),
                        "timestamp": commit.committed_date,
                        "is_savepoint": "savepoint" in commit.message.lower(),
                        "files_changed": len(commit.stats.files),
                    }
                )

            return commits

        except Exception as e:
            self.logger.error(f"Failed to get commit history for {path}: {e!s}")
            return []

    def init_repo(self, path: str) -> tuple[bool, str]:
        """Initialize a new Git repository"""
        if self.is_git_repo(path):
            return False, "Already a Git repository"

        try:
            repo = Repo.init(path)

            # Create initial .gitignore
            gitignore_path = Path(path) / ".gitignore"
            if not gitignore_path.exists():
                with open(gitignore_path, "w") as f:
                    f.write("# Common ignore patterns\n")
                    f.write("*.log\n")
                    f.write("*.tmp\n")
                    f.write(".env\n")
                    f.write("node_modules/\n")
                    f.write("vendor/\n")
                    f.write("__pycache__/\n")
                    f.write("*.pyc\n")
                    f.write(".DS_Store\n")
                    f.write("Thumbs.db\n")

            # Make initial commit
            repo.git.add(A=True)
            repo.index.commit("Initial commit")

            self.logger.info(f"Initialized Git repository at {path}")
            return True, "Git repository initialized successfully"

        except Exception as e:
            self.logger.error(f"Failed to initialize Git repo at {path}: {e!s}")
            return False, f"Failed to initialize repository: {e!s}"

    def push_to_remote(self, path: str, remote: str = "origin", branch: str | None = None) -> tuple[bool, str]:
        """Push changes to remote repository"""
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Check if remote exists
            if remote not in [r.name for r in repo.remotes]:
                return False, f"Remote '{remote}' not found"

            # Get current branch if not specified
            if not branch:
                try:
                    branch = repo.active_branch.name
                except TypeError:
                    return False, "Cannot push from detached HEAD"

            # Push
            origin = repo.remote(remote)
            origin.push(branch)

            self.logger.info(f"Pushed {path} to {remote}/{branch}")
            return True, f"Successfully pushed to {remote}/{branch}"

        except Exception as e:
            self.logger.error(f"Failed to push {path}: {e!s}")
            return False, f"Push failed: {e!s}"

    def pull_from_remote(self, path: str, remote: str = "origin", branch: str | None = None) -> tuple[bool, str]:
        """Pull changes from remote repository"""
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Check if remote exists
            if remote not in [r.name for r in repo.remotes]:
                return False, f"Remote '{remote}' not found"

            # Get current branch if not specified
            if not branch:
                try:
                    branch = repo.active_branch.name
                except TypeError:
                    return False, "Cannot pull to detached HEAD"

            # Pull
            origin = repo.remote(remote)
            origin.pull(branch)

            self.logger.info(f"Pulled {remote}/{branch} to {path}")
            return True, f"Successfully pulled from {remote}/{branch}"

        except Exception as e:
            self.logger.error(f"Failed to pull to {path}: {e!s}")
            return False, f"Pull failed: {e!s}"

    def get_diff(self, path: str, commit1: str | None = None, commit2: str | None = None) -> str:
        """Get diff between commits or working directory"""
        if not self.is_git_repo(path):
            return "Not a Git repository"

        try:
            repo = Repo(path)

            if commit1 and commit2:
                diff = repo.git.diff(commit1, commit2)
            elif commit1:
                diff = repo.git.diff(commit1)
            else:
                diff = repo.git.diff("HEAD")

            return str(diff)

        except Exception as e:
            self.logger.error(f"Failed to get diff for {path}: {e!s}")
            return f"Error: {e!s}"

    def restore_to_commit(self, path: str, commit_hash: str, mode: str = "hard") -> tuple[bool, str]:
        """Restore repository to a specific commit

        Args:
            path: Repository path
            commit_hash: Commit hash to restore to
            mode: 'hard', 'soft', or 'mixed' (default: 'hard')
        """
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Validate commit exists
            try:
                repo.commit(commit_hash)
            except Exception as e:
                return False, f"Commit {commit_hash} not found: {e}"

            # Check if there are uncommitted changes
            if repo.is_dirty() and mode == "hard":
                uncommitted_count = len(repo.index.diff(None)) + len(repo.untracked_files)
                if uncommitted_count > 0:
                    return False, f"Repository has {uncommitted_count} uncommitted changes. Commit or stash them first."

            # Perform the reset
            match mode:
                case "hard":
                    repo.git.reset("--hard", commit_hash)
                    message = f"Hard reset to commit {commit_hash[:8]} - All changes discarded"
                case "soft":
                    repo.git.reset("--soft", commit_hash)
                    message = f"Soft reset to commit {commit_hash[:8]} - Changes kept staged"
                case _:
                    repo.git.reset("--mixed", commit_hash)
                    message = f"Mixed reset to commit {commit_hash[:8]} - Changes kept unstaged"

            self.logger.info(f"Reset {path} to {commit_hash} ({mode})")
            return True, message

        except Exception as e:
            self.logger.error(f"Failed to restore {path} to {commit_hash}: {e!s}")
            return False, f"Error restoring to commit: {e!s}"

    def revert_commit(self, path: str, commit_hash: str) -> tuple[bool, str]:
        """Revert a specific commit (create new commit undoing changes)

        Args:
            path: Repository path
            commit_hash: Commit hash to revert
        """
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Validate commit exists
            try:
                repo.commit(commit_hash)
            except Exception as e:
                return False, f"Commit {commit_hash} not found: {e}"

            # Check if repository is clean
            if repo.is_dirty():
                return False, "Repository has uncommitted changes. Commit or stash them first."

            # Perform the revert
            repo.git.revert(commit_hash, "--no-edit")

            self.logger.info(f"Reverted commit {commit_hash} in {path}")
            return True, f"Successfully reverted commit {commit_hash[:8]}"

        except Exception as e:
            self.logger.error(f"Failed to revert commit {commit_hash} in {path}: {e!s}")
            return False, f"Error reverting commit: {e!s}"

    def create_branch_from_commit(self, path: str, commit_hash: str, branch_name: str) -> tuple[bool, str]:
        """Create a new branch from a specific commit

        Args:
            path: Repository path
            commit_hash: Commit hash to branch from
            branch_name: Name for the new branch
        """
        if not self.is_git_repo(path):
            return False, "Not a Git repository"

        try:
            repo = Repo(path)

            # Validate commit exists
            try:
                target_commit = repo.commit(commit_hash)
            except Exception as e:
                return False, f"Commit {commit_hash} not found: {e}"

            # Check if branch already exists
            if branch_name in [b.name for b in repo.branches]:
                return False, f"Branch '{branch_name}' already exists"

            # Create new branch from commit
            repo.create_head(branch_name, target_commit)

            self.logger.info(f"Created branch '{branch_name}' from {commit_hash} in {path}")
            return True, f"Created branch '{branch_name}' from commit {commit_hash[:8]}"

        except Exception as e:
            self.logger.error(f"Failed to create branch from {commit_hash} in {path}: {e!s}")
            return False, f"Error creating branch: {e!s}"

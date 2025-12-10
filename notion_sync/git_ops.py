"""
Git operations for version-controlled sync history.

Handles staging, committing, and pushing changes to the claude_memory
repository with atomic per-sync commits.
"""

import subprocess
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


class GitOperationsError(Exception):
    """Raised when git operations fail."""
    pass


class GitOperations:
    """
    Git operations for sync repository management.
    
    Provides atomic commit-per-sync with descriptive messages
    and push to remote.
    
    Attributes:
        repo_path: Path to git repository root.
        
    Example:
        >>> git = GitOperations("/path/to/claude_memory")
        >>> if git.has_changes():
        ...     git.commit_sync(pages_changed=5)
        ...     git.push()
    """
    
    def __init__(self, repo_path: str | Path):
        """
        Initialize git operations for repository.
        
        Args:
            repo_path: Path to git repository root directory.
            
        Raises:
            GitOperationsError: If path is not a git repository.
        """
        self.repo_path = Path(repo_path).resolve()
        
        if not (self.repo_path / ".git").exists():
            raise GitOperationsError(
                f"Not a git repository: {self.repo_path}"
            )
    
    def _run(
        self, 
        *args: str, 
        check: bool = True,
        capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """
        Run a git command in the repository.
        
        Args:
            *args: Git command arguments.
            check: Raise on non-zero exit.
            capture_output: Capture stdout/stderr.
            
        Returns:
            CompletedProcess instance.
            
        Raises:
            GitOperationsError: On command failure.
        """
        cmd = ["git", "-C", str(self.repo_path), *args]
        
        try:
            result = subprocess.run(
                cmd,
                check=check,
                capture_output=capture_output,
                text=True
            )
            return result
        except subprocess.CalledProcessError as e:
            raise GitOperationsError(
                f"Git command failed: {' '.join(cmd)}\n"
                f"stderr: {e.stderr}"
            )
    
    def has_changes(self) -> bool:
        """
        Check if working directory has uncommitted changes.
        
        Returns:
            True if there are staged or unstaged changes.
        """
        # Check for staged changes
        staged = self._run("diff", "--cached", "--quiet", check=False)
        if staged.returncode != 0:
            return True
        
        # Check for unstaged changes
        unstaged = self._run("diff", "--quiet", check=False)
        if unstaged.returncode != 0:
            return True
        
        # Check for untracked files in pages directory
        untracked = self._run("ls-files", "--others", "--exclude-standard", "pages/")
        return bool(untracked.stdout.strip())
    
    def get_changed_files(self) -> List[str]:
        """
        Get list of changed files (staged, unstaged, and untracked).
        
        Returns:
            List of changed file paths relative to repo root.
        """
        files = set()
        
        # Staged changes
        staged = self._run("diff", "--cached", "--name-only")
        files.update(staged.stdout.strip().split("\n"))
        
        # Unstaged changes
        unstaged = self._run("diff", "--name-only")
        files.update(unstaged.stdout.strip().split("\n"))
        
        # Untracked files
        untracked = self._run("ls-files", "--others", "--exclude-standard")
        files.update(untracked.stdout.strip().split("\n"))
        
        # Filter empty strings
        return [f for f in files if f]
    
    def stage_all(self) -> None:
        """Stage all changes including new files."""
        self._run("add", ".")
    
    def stage_pages(self) -> None:
        """Stage only the pages directory and database."""
        self._run("add", "pages/")
        
        # Also stage database if it exists
        db_path = self.repo_path / "memory.db"
        if db_path.exists():
            self._run("add", "memory.db")
    
    def commit_sync(
        self, 
        pages_changed: int,
        direction: str = "sync",
        message: Optional[str] = None
    ) -> str:
        """
        Create a sync commit with standardized message.
        
        Args:
            pages_changed: Number of pages in this sync.
            direction: 'pull', 'push', or 'sync'.
            message: Optional custom message suffix.
            
        Returns:
            Commit hash.
            
        Raises:
            GitOperationsError: If commit fails.
        """
        timestamp = datetime.now().isoformat(timespec="seconds")
        
        commit_msg = f"Sync {timestamp} | {pages_changed} pages | {direction}"
        if message:
            commit_msg += f" | {message}"
        
        # Stage changes first
        self.stage_all()
        
        # Check if there's anything to commit
        if not self.has_changes():
            logger.info("No changes to commit")
            return self.get_head_commit()
        
        self._run("commit", "-m", commit_msg)
        
        return self.get_head_commit()
    
    def push(self, remote: str = "origin", branch: Optional[str] = None) -> bool:
        """
        Push commits to remote.
        
        Args:
            remote: Remote name (default: origin).
            branch: Branch name. If None, pushes current branch.
            
        Returns:
            True if push succeeded.
            
        Raises:
            GitOperationsError: On push failure.
        """
        if branch:
            self._run("push", remote, branch)
        else:
            self._run("push", remote)
        
        return True
    
    def pull(self, remote: str = "origin", branch: Optional[str] = None) -> bool:
        """
        Pull changes from remote.
        
        Args:
            remote: Remote name (default: origin).
            branch: Branch name. If None, pulls current branch.
            
        Returns:
            True if pull succeeded.
        """
        if branch:
            self._run("pull", remote, branch)
        else:
            self._run("pull", remote)
        
        return True
    
    def get_head_commit(self) -> str:
        """Get the current HEAD commit hash."""
        result = self._run("rev-parse", "HEAD")
        return result.stdout.strip()
    
    def get_current_branch(self) -> str:
        """Get the current branch name."""
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()
    
    def get_status(self) -> str:
        """Get human-readable git status."""
        result = self._run("status", "--short")
        return result.stdout.strip()
    
    def get_last_sync_commit(self) -> Optional[Tuple[str, datetime, int]]:
        """
        Get info about the last sync commit.
        
        Returns:
            Tuple of (commit_hash, timestamp, pages_count) or None.
        """
        try:
            result = self._run(
                "log", "-1", 
                "--grep=Sync", 
                "--format=%H|%ci|%s"
            )
            
            if not result.stdout.strip():
                return None
            
            parts = result.stdout.strip().split("|")
            if len(parts) < 3:
                return None
            
            commit_hash = parts[0]
            timestamp = datetime.fromisoformat(parts[1].strip())
            
            # Parse pages count from message
            message = parts[2]
            pages_count = 0
            if "pages" in message:
                try:
                    # Extract number before "pages"
                    pages_part = message.split("pages")[0].split("|")[-1].strip()
                    pages_count = int(pages_part)
                except (ValueError, IndexError):
                    pass
            
            return (commit_hash, timestamp, pages_count)
        
        except GitOperationsError:
            return None
    
    def init_repo(self) -> None:
        """
        Initialize a new git repository if not exists.
        
        Creates .gitignore with sensible defaults.
        """
        if (self.repo_path / ".git").exists():
            logger.info("Repository already initialized")
            return
        
        subprocess.run(
            ["git", "init", str(self.repo_path)],
            check=True,
            capture_output=True
        )
        
        # Create .gitignore
        gitignore_content = """
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
.env

# Logs
*.log
logs/

# OS
.DS_Store
Thumbs.db

# IDE
.idea/
.vscode/
*.swp
*.swo

# Temporary
*.tmp
*.temp
.cache/
"""
        
        gitignore_path = self.repo_path / ".gitignore"
        gitignore_path.write_text(gitignore_content.strip())
        
        logger.info(f"Initialized git repository at {self.repo_path}")


if __name__ == "__main__":
    import sys
    
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "."
    
    try:
        git = GitOperations(repo_path)
        print(f"Repository: {git.repo_path}")
        print(f"Branch: {git.get_current_branch()}")
        print(f"Has changes: {git.has_changes()}")
        print(f"Status:\n{git.get_status()}")
        
        last_sync = git.get_last_sync_commit()
        if last_sync:
            print(f"Last sync: {last_sync[1]} ({last_sync[2]} pages)")
    
    except GitOperationsError as e:
        print(f"Error: {e}")
        sys.exit(1)

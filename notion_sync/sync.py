"""
Core synchronization orchestration for Notion ↔ Local ↔ Git.

Coordinates bidirectional sync with Notion-authoritative conflict resolution,
atomic git commits per sync, and agent-accessible interface.
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict

from notion_sync.database import SyncDatabase, PageRecord
from notion_sync.notion_client import NotionClient, NotionPage, NotionClientError
from notion_sync.git_ops import GitOperations, GitOperationsError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    direction: str
    pages_synced: int
    pages_skipped: int
    errors: List[str]
    commit_hash: Optional[str]
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            **asdict(self),
            "timestamp": self.timestamp.isoformat()
        }


class NotionSync:
    """
    Bidirectional sync orchestrator for Notion ↔ Local ↔ Git.
    
    Implements the sync algorithm:
        1. Fetch Notion page list (id + last_edited only)
        2. Compare against DB: hash mismatch or edited > synced?
        3. Fetch full content only for changed pages
        4. Write files, update DB
        5. Single git commit + push
        6. Update last_synced timestamps
    
    Conflict resolution: Notion-authoritative (human edits win).
    
    Attributes:
        repo_path: Path to git repository.
        db: SyncDatabase instance.
        notion: NotionClient instance.
        git: GitOperations instance.
        
    Example:
        >>> sync = NotionSync(
        ...     repo_path="/path/to/claude_memory",
        ...     database_id="72d9345fc671480cb9b72d4bd22baf74"
        ... )
        >>> result = sync.sync_now()
        >>> print(f"Synced {result.pages_synced} pages")
    """
    
    def __init__(
        self,
        repo_path: str | Path,
        database_id: str,
        notion_token: Optional[str] = None
    ):
        """
        Initialize sync orchestrator.
        
        Args:
            repo_path: Path to git repository root.
            database_id: Notion database ID to sync.
            notion_token: Optional API token (uses Keychain if not provided).
        """
        self.repo_path = Path(repo_path).resolve()
        self.pages_dir = self.repo_path / "pages"
        self.db_path = self.repo_path / "memory.db"
        
        # Ensure directories exist
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.db = SyncDatabase(self.db_path)
        self.db.init()
        
        self.notion = NotionClient(
            database_id=database_id,
            token=notion_token
        )
        
        self.git = GitOperations(self.repo_path)
        
        logger.info(f"Initialized NotionSync for {self.repo_path}")
    
    def sync_now(self, push: bool = True) -> SyncResult:
        """
        Execute full bidirectional sync.
        
        Args:
            push: Whether to push to git remote after commit.
            
        Returns:
            SyncResult with operation details.
        """
        logger.info("Starting sync...")
        
        errors = []
        pages_synced = 0
        pages_skipped = 0
        
        try:
            # Pull from Notion (Notion → Local)
            pull_result = self._pull_from_notion()
            pages_synced += pull_result["synced"]
            pages_skipped += pull_result["skipped"]
            errors.extend(pull_result["errors"])
            
            # Commit and push if there are changes
            commit_hash = None
            if self.git.has_changes():
                commit_hash = self.git.commit_sync(
                    pages_changed=pages_synced,
                    direction="pull"
                )
                logger.info(f"Committed: {commit_hash[:8]}")
                
                if push:
                    try:
                        self.git.push()
                        logger.info("Pushed to remote")
                    except GitOperationsError as e:
                        errors.append(f"Push failed: {e}")
            else:
                logger.info("No changes to commit")
            
            # Log sync operation
            self.db.log_sync(
                pages_synced=pages_synced,
                direction="pull",
                status="success" if not errors else "partial",
                message="; ".join(errors) if errors else ""
            )
            
            return SyncResult(
                success=len(errors) == 0,
                direction="pull",
                pages_synced=pages_synced,
                pages_skipped=pages_skipped,
                errors=errors,
                commit_hash=commit_hash,
                timestamp=datetime.utcnow()
            )
        
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            self.db.log_sync(
                pages_synced=0,
                direction="pull",
                status="error",
                message=str(e)
            )
            return SyncResult(
                success=False,
                direction="pull",
                pages_synced=0,
                pages_skipped=0,
                errors=[str(e)],
                commit_hash=None,
                timestamp=datetime.utcnow()
            )
    
    def _pull_from_notion(self) -> Dict[str, Any]:
        """
        Pull changes from Notion to local.
        
        Returns:
            Dict with 'synced', 'skipped', and 'errors' counts.
        """
        result = {"synced": 0, "skipped": 0, "errors": []}
        
        # Get last sync time for incremental fetch
        last_sync = self.db.get_last_sync_time()
        logger.info(f"Last sync: {last_sync or 'never'}")
        
        # Fetch pages from Notion
        try:
            pages = self.notion.get_database_pages(since=last_sync)
            logger.info(f"Found {len(pages)} pages to check")
        except NotionClientError as e:
            result["errors"].append(f"Failed to fetch pages: {e}")
            return result
        
        for page in pages:
            try:
                # Fetch content for this page
                content = self.notion.get_page_content(page.id)
                content_hash = SyncDatabase.compute_hash(content)
                
                # Check if sync needed
                if not self.db.needs_sync(page.id, page.last_edited, content_hash):
                    result["skipped"] += 1
                    continue
                
                # Write content to file
                self._write_page_file(page, content)
                
                # Update database
                record = PageRecord(
                    notion_id=page.id,
                    title=page.title,
                    parent_id=page.parent_id,
                    content_hash=content_hash,
                    last_edited=page.last_edited,
                    last_synced=datetime.utcnow(),
                    status="synced"
                )
                self.db.upsert_page(record)
                
                result["synced"] += 1
                logger.debug(f"Synced: {page.title}")
            
            except Exception as e:
                result["errors"].append(f"Failed to sync {page.id}: {e}")
                logger.error(f"Error syncing page {page.id}: {e}")
        
        return result
    
    def _write_page_file(self, page: NotionPage, content: str) -> Path:
        """
        Write page content to markdown file.
        
        Args:
            page: NotionPage object.
            content: Markdown content.
            
        Returns:
            Path to written file.
        """
        # Sanitize title for filename
        safe_title = "".join(
            c for c in page.title 
            if c.isalnum() or c in " -_"
        ).strip()[:50] or "untitled"
        
        # Use notion ID to ensure uniqueness
        filename = f"{safe_title}_{page.id[:8]}.md"
        filepath = self.pages_dir / filename
        
        # Build file content with frontmatter
        frontmatter = f"""---
notion_id: {page.id}
title: {page.title}
last_edited: {page.last_edited.isoformat()}
url: {page.url}
---

"""
        
        filepath.write_text(frontmatter + content, encoding="utf-8")
        return filepath
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current sync status.
        
        Returns:
            Dict with status information.
        """
        last_sync = self.db.get_last_sync_time()
        last_commit = self.git.get_last_sync_commit()
        
        return {
            "repo_path": str(self.repo_path),
            "database_id": self.notion.database_id,
            "last_sync": last_sync.isoformat() if last_sync else None,
            "last_commit": {
                "hash": last_commit[0][:8] if last_commit else None,
                "timestamp": last_commit[1].isoformat() if last_commit else None,
                "pages": last_commit[2] if last_commit else 0
            },
            "total_pages": len(self.db.get_all_pages()),
            "pending_changes": self.git.has_changes(),
            "current_branch": self.git.get_current_branch()
        }
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search synced pages by title.
        
        Args:
            query: Search term.
            
        Returns:
            List of matching page records as dicts.
        """
        records = self.db.search(query)
        results = []
        
        for record in records:
            # Try to read content from file
            content = ""
            for filepath in self.pages_dir.glob(f"*_{record.notion_id[:8]}.md"):
                content = filepath.read_text(encoding="utf-8")
                break
            
            results.append({
                "notion_id": record.notion_id,
                "title": record.title,
                "last_edited": record.last_edited.isoformat(),
                "status": record.status,
                "content_preview": content[:500] if content else ""
            })
        
        return results
    
    def get_page(self, notion_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific page by Notion ID.
        
        Args:
            notion_id: Notion page UUID.
            
        Returns:
            Page data dict or None.
        """
        record = self.db.get_page(notion_id)
        if not record:
            return None
        
        # Read content from file
        content = ""
        for filepath in self.pages_dir.glob(f"*_{notion_id[:8]}.md"):
            content = filepath.read_text(encoding="utf-8")
            break
        
        return {
            "notion_id": record.notion_id,
            "title": record.title,
            "parent_id": record.parent_id,
            "last_edited": record.last_edited.isoformat(),
            "last_synced": record.last_synced.isoformat(),
            "status": record.status,
            "content": content
        }


def main():
    """Command-line interface for notion-sync."""
    parser = argparse.ArgumentParser(
        description="Bidirectional Notion ↔ Git sync"
    )
    parser.add_argument(
        "--repo", "-r",
        default=os.environ.get("NOTION_SYNC_REPO", "."),
        help="Path to git repository"
    )
    parser.add_argument(
        "--database", "-d",
        default=os.environ.get("NOTION_DATABASE_ID", "72d9345fc671480cb9b72d4bd22baf74"),
        help="Notion database ID"
    )
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show sync status"
    )
    parser.add_argument(
        "--search", "-q",
        metavar="QUERY",
        help="Search synced pages"
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Don't push to git remote"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        sync = NotionSync(
            repo_path=args.repo,
            database_id=args.database
        )
        
        if args.status:
            status = sync.get_status()
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                print(f"Repository: {status['repo_path']}")
                print(f"Database: {status['database_id']}")
                print(f"Total pages: {status['total_pages']}")
                print(f"Last sync: {status['last_sync'] or 'never'}")
                print(f"Pending changes: {status['pending_changes']}")
                print(f"Branch: {status['current_branch']}")
        
        elif args.search:
            results = sync.search(args.search)
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"Found {len(results)} results:")
                for r in results:
                    print(f"  - {r['title']} ({r['notion_id'][:8]})")
        
        else:
            # Run sync
            result = sync.sync_now(push=not args.no_push)
            
            if args.json:
                print(json.dumps(result.to_dict(), indent=2))
            else:
                status_icon = "✓" if result.success else "✗"
                print(f"{status_icon} Sync completed")
                print(f"  Pages synced: {result.pages_synced}")
                print(f"  Pages skipped: {result.pages_skipped}")
                if result.commit_hash:
                    print(f"  Commit: {result.commit_hash[:8]}")
                if result.errors:
                    print(f"  Errors: {len(result.errors)}")
                    for err in result.errors:
                        print(f"    - {err}")
            
            sys.exit(0 if result.success else 1)
    
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

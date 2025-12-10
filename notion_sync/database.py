"""
SQLite database operations for Notion sync state management.

Handles page metadata storage, sync status tracking, and change detection
using content hashing for efficient dirty-checking.
"""

import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from contextlib import contextmanager


@dataclass
class PageRecord:
    """Represents a synced page record in the database."""
    notion_id: str
    title: str
    parent_id: Optional[str]
    content_hash: str
    last_edited: datetime
    last_synced: datetime
    status: str = "synced"


class SyncDatabase:
    """
    SQLite database for tracking Notion page sync state.
    
    Provides CRUD operations for page records and change detection
    via content hashing.
    
    Attributes:
        db_path: Path to SQLite database file.
        
    Example:
        >>> db = SyncDatabase("/path/to/memory.db")
        >>> db.init()
        >>> db.upsert_page(page_record)
        >>> changed = db.get_pages_needing_sync()
    """
    
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS pages (
            notion_id      TEXT PRIMARY KEY,
            title          TEXT,
            parent_id      TEXT,
            content_hash   TEXT,
            last_edited    TIMESTAMP,
            last_synced    TIMESTAMP,
            status         TEXT DEFAULT 'synced'
        );
        
        CREATE INDEX IF NOT EXISTS idx_last_edited ON pages(last_edited);
        CREATE INDEX IF NOT EXISTS idx_status ON pages(status);
        
        CREATE TABLE IF NOT EXISTS sync_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pages_synced   INTEGER,
            direction      TEXT,
            status         TEXT,
            message        TEXT
        );
    """
    
    def __init__(self, db_path: str | Path):
        """
        Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file. Created if not exists.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def _connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def init(self) -> None:
        """Initialize database schema. Safe to call multiple times."""
        with self._connection() as conn:
            conn.executescript(self.SCHEMA)
    
    def get_page(self, notion_id: str) -> Optional[PageRecord]:
        """
        Retrieve a page record by Notion ID.
        
        Args:
            notion_id: The Notion page UUID.
            
        Returns:
            PageRecord if found, None otherwise.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pages WHERE notion_id = ?",
                (notion_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_record(row)
            return None
    
    def get_all_pages(self) -> List[PageRecord]:
        """Retrieve all page records."""
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM pages ORDER BY last_edited DESC")
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def get_pages_by_status(self, status: str) -> List[PageRecord]:
        """
        Retrieve pages with a specific sync status.
        
        Args:
            status: One of 'synced', 'pending', 'conflict', 'deleted'.
            
        Returns:
            List of matching PageRecord objects.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pages WHERE status = ?",
                (status,)
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def upsert_page(self, page: PageRecord) -> None:
        """
        Insert or update a page record.
        
        Args:
            page: PageRecord to upsert.
        """
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO pages (notion_id, title, parent_id, content_hash, 
                                   last_edited, last_synced, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(notion_id) DO UPDATE SET
                    title = excluded.title,
                    parent_id = excluded.parent_id,
                    content_hash = excluded.content_hash,
                    last_edited = excluded.last_edited,
                    last_synced = excluded.last_synced,
                    status = excluded.status
            """, (
                page.notion_id,
                page.title,
                page.parent_id,
                page.content_hash,
                page.last_edited,
                page.last_synced,
                page.status
            ))
    
    def update_sync_status(self, notion_id: str, status: str) -> None:
        """
        Update the sync status of a page.
        
        Args:
            notion_id: The Notion page UUID.
            status: New status value.
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE pages SET status = ?, last_synced = ? WHERE notion_id = ?",
                (status, datetime.utcnow(), notion_id)
            )
    
    def mark_synced(self, notion_id: str, content_hash: str) -> None:
        """
        Mark a page as successfully synced with new content hash.
        
        Args:
            notion_id: The Notion page UUID.
            content_hash: SHA256 hash of current content.
        """
        with self._connection() as conn:
            conn.execute("""
                UPDATE pages 
                SET status = 'synced', 
                    last_synced = ?, 
                    content_hash = ?
                WHERE notion_id = ?
            """, (datetime.utcnow(), content_hash, notion_id))
    
    def delete_page(self, notion_id: str) -> None:
        """Remove a page record from the database."""
        with self._connection() as conn:
            conn.execute("DELETE FROM pages WHERE notion_id = ?", (notion_id,))
    
    def needs_sync(self, notion_id: str, last_edited: datetime, content_hash: str) -> bool:
        """
        Check if a page needs syncing based on edit time and content hash.
        
        Args:
            notion_id: The Notion page UUID.
            last_edited: Last edit timestamp from Notion.
            content_hash: SHA256 hash of current content.
            
        Returns:
            True if page is new or has changed, False otherwise.
        """
        existing = self.get_page(notion_id)
        if not existing:
            return True
        if existing.content_hash != content_hash:
            return True
        if last_edited > existing.last_synced:
            return True
        return False
    
    def log_sync(self, pages_synced: int, direction: str, 
                 status: str, message: str = "") -> None:
        """
        Log a sync operation for audit trail.
        
        Args:
            pages_synced: Number of pages processed.
            direction: 'pull' or 'push'.
            status: 'success' or 'error'.
            message: Optional status message.
        """
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO sync_log (pages_synced, direction, status, message)
                VALUES (?, ?, ?, ?)
            """, (pages_synced, direction, status, message))
    
    def get_last_sync_time(self) -> Optional[datetime]:
        """Get timestamp of most recent successful sync."""
        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT MAX(sync_time) as last_sync 
                FROM sync_log 
                WHERE status = 'success'
            """)
            row = cursor.fetchone()
            return row["last_sync"] if row else None
    
    def search(self, query: str) -> List[PageRecord]:
        """
        Search pages by title (case-insensitive).
        
        Args:
            query: Search term to match against titles.
            
        Returns:
            List of matching PageRecord objects.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pages WHERE title LIKE ? ORDER BY last_edited DESC",
                (f"%{query}%",)
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]
    
    def _row_to_record(self, row: sqlite3.Row) -> PageRecord:
        """Convert database row to PageRecord dataclass."""
        return PageRecord(
            notion_id=row["notion_id"],
            title=row["title"],
            parent_id=row["parent_id"],
            content_hash=row["content_hash"],
            last_edited=row["last_edited"],
            last_synced=row["last_synced"],
            status=row["status"]
        )
    
    @staticmethod
    def compute_hash(content: str) -> str:
        """
        Compute SHA256 hash of content for change detection.
        
        Args:
            content: String content to hash.
            
        Returns:
            Hexadecimal SHA256 hash string.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def init_database(db_path: str = "memory.db") -> SyncDatabase:
    """
    Initialize and return a SyncDatabase instance.
    
    Args:
        db_path: Path to database file.
        
    Returns:
        Initialized SyncDatabase instance.
    """
    db = SyncDatabase(db_path)
    db.init()
    return db


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        db_path = sys.argv[2] if len(sys.argv) > 2 else "memory.db"
        db = init_database(db_path)
        print(f"Database initialized: {db.db_path}")
    else:
        print("Usage: python -m notion_sync.database init [db_path]")

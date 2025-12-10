"""
Automated Notion Database Updater

A Python automation for LLM agent integration that maintains bidirectional
synchronization between a Notion workspace, a local SQLite database, and
a git repository.

Repository: https://github.com/badMade/claude_memory.git
Notion KB: https://www.notion.so/72d9345fc671480cb9b72d4bd22baf74
"""

__version__ = "1.0.0"
__author__ = "Geoff Plymale"

from notion_sync.sync import NotionSync
from notion_sync.database import SyncDatabase
from notion_sync.notion_client import NotionClient
from notion_sync.git_ops import GitOperations

__all__ = [
    "NotionSync",
    "SyncDatabase", 
    "NotionClient",
    "GitOperations",
]

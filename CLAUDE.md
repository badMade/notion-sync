# CLAUDE.md

## Project Overview

**notion-sync** is a Python automation tool for LLM agent integration that maintains bidirectional synchronization between a Notion workspace, a local SQLite database, and a git repository. It provides cloud memory access for AI agents with full version control and human-editable source of truth in Notion.

Repository: https://github.com/badMade/claude_memory.git

## Quick Reference

### Running the Sync

```bash
# Manual sync (from project root)
python -m notion_sync.sync

# With shell script
./bin/notion-sync-now

# Check status
python -m notion_sync.sync --status

# Search pages
python -m notion_sync.sync --search "query"

# JSON output
python -m notion_sync.sync --status --json
```

### Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Initialize database
python -m notion_sync.database init
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NOTION_TOKEN` | Notion API token | Keychain lookup |
| `NOTION_SYNC_REPO` | Repository path | Current directory |
| `NOTION_DATABASE_ID` | Notion database ID | `<YOUR_DATABASE_ID>` |

## Architecture

```
Notion <-> Local Database <-> Git Repository
           (SQLite)          (claude_memory)
```

**Sync Direction:**
- **Pull:** Notion -> Local -> Git (capture human edits)
- **Push:** Git -> Local -> Notion (persist agent content)

**Conflict Resolution:** Notion-authoritative (human edits always win)

## Code Structure

```
notion_sync/
├── __init__.py          # Package exports (NotionSync, SyncDatabase, etc.)
├── sync.py              # Core sync orchestration - main entry point
├── notion_client.py     # Notion API wrapper
├── database.py          # SQLite operations
└── git_ops.py           # Git commit/push operations

bin/
├── notion-sync.sh       # Cron entry point script
└── notion-sync-now      # Agent trigger script

pages/                   # Synced page content (git-tracked, markdown files)
```

## Key Classes

| Class | File | Purpose |
|-------|------|---------|
| `NotionSync` | `sync.py` | Main orchestrator - use this for programmatic access |
| `SyncDatabase` | `database.py` | SQLite operations, page records, sync logging |
| `NotionClient` | `notion_client.py` | Notion API wrapper |
| `GitOperations` | `git_ops.py` | Git commit, push, status operations |

## Python API Usage

```python
from notion_sync import NotionSync

sync = NotionSync(
    repo_path="/path/to/repo",
    database_id="<YOUR_NOTION_DATABASE_ID>"
)

# Sync
result = sync.sync_now()

# Status
status = sync.get_status()

# Search
results = sync.search("query")

# Get specific page
page = sync.get_page("notion-page-id")
```

## Database Schema

The SQLite database (`memory.db`) contains:

- **pages**: Page records with `notion_id`, `title`, `content_hash`, `last_edited`, `last_synced`, `status`
- **sync_log**: Sync operation history with timestamps, counts, and status

## Development Notes

- **Python version:** 3.10+ (uses `|` union types)
- **Dependencies:** `requests` (HTTP client), optional `typing-extensions`
- **Token storage:** macOS Keychain via `security` command, or `NOTION_TOKEN` env var
- **Lock file:** `/tmp/notion-sync.lock` prevents concurrent runs

## Common Tasks

### Adding a new sync feature
1. Modify `sync.py` for orchestration logic
2. Update `notion_client.py` for Notion API calls
3. Update `database.py` for schema/query changes
4. Update `git_ops.py` for git operations

### Testing sync locally
```bash
# Dry run without pushing
python -m notion_sync.sync --no-push --verbose
```

### Debugging
```bash
# Verbose mode shows debug logs
python -m notion_sync.sync --verbose

# Check sync logs in database
sqlite3 memory.db "SELECT * FROM sync_log ORDER BY sync_time DESC LIMIT 10;"
```

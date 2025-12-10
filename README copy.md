# Automated Notion Database Updater

**Repository:** `https://github.com/badMade/claude_memory.git`

**Notion Knowledge Base:** `https://www.notion.so/72d9345fc671480cb9b72d4bd22baf74`

## Overview

A Python automation designed for LLM agent integration that maintains bidirectional synchronization between a Notion workspace, a local SQLite database, and a git repository. Provides cloud memory access for AI agents with full version control and human-editable source of truth in Notion.

## Sync Architecture

```
Notion ←→ Local Database ←→ Git Repository
         (SQLite)         (claude_memory)
```

| Direction | Flow | Purpose |
|-----------|------|---------|
| Pull | Notion → Local → Git | Capture human edits from Notion |
| Push | Git → Local → Notion | Persist agent-generated content |

**Conflict Resolution:** Notion-authoritative — human edits always win.

## Features

- **Change Detection** — Content hashing for efficient dirty-checking
- **Incremental Sync** — Only fetches changed pages to minimize API calls
- **Atomic Commits** — One git commit per sync operation
- **Agent Interface** — Python API for LLM agent integration
- **Hybrid Triggers** — Cron baseline + on-demand agent invocation
- **Secure Token Storage** — macOS Keychain integration

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/badMade/claude_memory.git
cd claude_memory
```

### 2. Install Dependencies

```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Store Notion API Token

```bash
# Add token to macOS Keychain
security add-generic-password -s "notion-api" -a "$(whoami)" -w "your-notion-api-token"

# Verify it's stored
security find-generic-password -s "notion-api" -w
```

### 4. Initialize Database

```bash
python -m notion_sync.database init
```

### 5. Make Scripts Executable

```bash
chmod +x bin/notion-sync.sh
chmod +x bin/notion-sync-now
```

### 6. Set Up Scheduled Sync

**Option A: launchd (macOS recommended)**

```bash
# Copy plist to LaunchAgents
cp com.geoff.notion-sync.plist ~/Library/LaunchAgents/

# Load the job
launchctl load ~/Library/LaunchAgents/com.geoff.notion-sync.plist

# Verify it's loaded
launchctl list | grep notion-sync
```

**Option B: cron**

```bash
# Edit crontab
crontab -e

# Add line (every 15 min during working hours):
*/15 9-22 * * * /path/to/claude_memory/bin/notion-sync.sh >> ~/logs/notion-sync.log 2>&1
```

### 7. Create Log Directory

```bash
mkdir -p ~/logs
```

## Usage

### Manual Sync

```bash
# Run sync
./bin/notion-sync-now

# Or directly via Python
python -m notion_sync.sync
```

### Check Status

```bash
python -m notion_sync.sync --status
```

Output:
```
Repository: /Users/geoffplymale/Documents/GitHub/claude_memory
Database: 72d9345fc671480cb9b72d4bd22baf74
Total pages: 42
Last sync: 2025-01-15T10:30:00
Pending changes: False
Branch: main
```

### Search Pages

```bash
python -m notion_sync.sync --search "project planning"
```

### JSON Output

```bash
python -m notion_sync.sync --status --json
python -m notion_sync.sync --search "query" --json
```

## Python API

For LLM agent integration:

```python
from notion_sync import NotionSync

# Initialize
sync = NotionSync(
    repo_path="/path/to/claude_memory",
    database_id="72d9345fc671480cb9b72d4bd22baf74"
)

# Trigger sync
result = sync.sync_now()
print(f"Synced {result.pages_synced} pages")

# Get status
status = sync.get_status()
print(f"Last sync: {status['last_sync']}")

# Search content
results = sync.search("project")
for page in results:
    print(f"- {page['title']}")

# Get specific page
page = sync.get_page("notion-page-id")
print(page['content'])
```

## File Structure

```
claude_memory/
├── notion_sync/
│   ├── __init__.py           # Package exports
│   ├── sync.py               # Core sync orchestration
│   ├── notion_client.py      # Notion API wrapper
│   ├── database.py           # SQLite operations
│   └── git_ops.py            # Git commit/push
├── bin/
│   ├── notion-sync.sh        # Cron entry point
│   └── notion-sync-now       # Agent trigger
├── pages/                    # Synced page content (git-tracked)
├── memory.db                 # SQLite database
├── requirements.txt
├── com.geoff.notion-sync.plist
└── README.md
```

## Database Schema

```sql
CREATE TABLE pages (
    notion_id      TEXT PRIMARY KEY,
    title          TEXT,
    parent_id      TEXT,
    content_hash   TEXT,       -- SHA256 for change detection
    last_edited    TIMESTAMP,  -- from Notion API
    last_synced    TIMESTAMP,  -- local sync timestamp
    status         TEXT DEFAULT 'synced'
);

CREATE TABLE sync_log (
    id             INTEGER PRIMARY KEY,
    sync_time      TIMESTAMP,
    pages_synced   INTEGER,
    direction      TEXT,
    status         TEXT,
    message        TEXT
);
```

## Sync Algorithm

```
1. Fetch Notion page list (id + last_edited only)
2. Compare against DB: hash mismatch or edited > synced?
3. Fetch full content only for changed pages
4. Write files to pages/ directory
5. Update database records
6. Single git commit + push
7. Update last_synced timestamps
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NOTION_TOKEN` | Notion API token | Keychain lookup |
| `NOTION_SYNC_REPO` | Repository path | Current directory |
| `NOTION_DATABASE_ID` | Notion database ID | `72d9345fc671480cb9b72d4bd22baf74` |

### Security

The sync script validates:
- Running as expected user (`geoffplymale`)
- Lock file prevents concurrent runs
- Token retrieved from Keychain (not stored in files)

## Troubleshooting

### Token Not Found

```bash
# Store token in Keychain
security add-generic-password -s "notion-api" -a "$(whoami)" -w "secret_xxx"

# Or export as environment variable
export NOTION_TOKEN="secret_xxx"
```

### Permission Denied

```bash
chmod +x bin/notion-sync.sh bin/notion-sync-now
```

### launchd Job Not Running

```bash
# Check if loaded
launchctl list | grep notion

# Check logs
tail -f ~/logs/notion-sync.log
tail -f ~/logs/notion-sync-error.log

# Unload and reload
launchctl unload ~/Library/LaunchAgents/com.geoff.notion-sync.plist
launchctl load ~/Library/LaunchAgents/com.geoff.notion-sync.plist
```

### Database Locked

```bash
# Remove stale lock file
rm -f /tmp/notion-sync.lock
```

## MCP Integration

To expose sync functionality via MCP for Claude Desktop:

```json
{
  "mcpServers": {
    "notion-sync": {
      "command": "python",
      "args": ["-m", "notion_sync.mcp_server"],
      "cwd": "/path/to/claude_memory"
    }
  }
}
```

## License

MIT

## Author

Geoff Plymale

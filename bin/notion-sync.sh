#!/bin/bash
#
# notion-sync.sh - Cron entry point for Notion sync
#
# Usage: 
#   ./notion-sync.sh                    # Normal sync
#   ./notion-sync.sh --status           # Check status only
#   ./notion-sync.sh --search "query"   # Search pages
#
# Cron example (every 15 min during working hours):
#   */15 9-22 * * * /path/to/notion-sync.sh >> ~/logs/notion-sync.log 2>&1
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${NOTION_SYNC_REPO:-$(dirname "$SCRIPT_DIR")}"
LOG_DIR="${HOME}/logs"
LOCK_FILE="/tmp/notion-sync.lock"
EXPECTED_USER="geoffplymale"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Timestamp for logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Security: Validate user
if [[ "$(whoami)" != "$EXPECTED_USER" ]]; then
    error "Must run as $EXPECTED_USER"
    exit 1
fi

# Retrieve Notion token from macOS Keychain
get_notion_token() {
    local token
    
    # Try Keychain first
    if command -v security &> /dev/null; then
        token=$(security find-generic-password -s "notion-api" -w 2>/dev/null) || true
    fi
    
    # Fallback to environment variable
    if [[ -z "${token:-}" ]]; then
        token="${NOTION_TOKEN:-}"
    fi
    
    if [[ -z "$token" ]]; then
        error "No Notion API token found. Store in Keychain or set NOTION_TOKEN"
        exit 1
    fi
    
    echo "$token"
}

# Acquire lock to prevent concurrent runs
acquire_lock() {
    exec 200>"$LOCK_FILE"
    
    if ! flock -n 200; then
        log "Another sync is already running"
        exit 0
    fi
    
    # Write PID to lock file
    echo $$ >&200
}

# Main sync function
run_sync() {
    log "Starting Notion sync..."
    
    # Export token for Python script
    export NOTION_TOKEN
    NOTION_TOKEN=$(get_notion_token)
    
    # Change to repo directory
    cd "$REPO_DIR"
    
    # Activate virtual environment if exists
    if [[ -f "venv/bin/activate" ]]; then
        source "venv/bin/activate"
    fi
    
    # Run sync
    python -m notion_sync.sync "$@"
    local exit_code=$?
    
    if [[ $exit_code -eq 0 ]]; then
        log "Sync completed successfully"
    else
        error "Sync failed with exit code $exit_code"
    fi
    
    return $exit_code
}

# Entry point
main() {
    acquire_lock
    
    # Trap to release lock on exit
    trap 'rm -f "$LOCK_FILE"' EXIT
    
    run_sync "$@"
}

main "$@"

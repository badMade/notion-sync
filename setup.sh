#!/bin/bash
#
# setup.sh - First-time setup for Notion Sync
#
# Usage: ./setup.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Notion Sync Setup ==="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
echo "✓ Python version: $PYTHON_VERSION"

# Create virtual environment
if [[ ! -d "$SCRIPT_DIR/venv" ]]; then
    echo "→ Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment exists"
fi

# Activate and install dependencies
echo "→ Installing dependencies..."
source "$SCRIPT_DIR/venv/bin/activate"
pip install -q -r "$SCRIPT_DIR/requirements.txt"
echo "✓ Dependencies installed"

# Make scripts executable
echo "→ Setting script permissions..."
chmod +x "$SCRIPT_DIR/bin/notion-sync.sh"
chmod +x "$SCRIPT_DIR/bin/notion-sync-now"
echo "✓ Scripts are executable"

# Create directories
echo "→ Creating directories..."
mkdir -p "$SCRIPT_DIR/pages"
mkdir -p ~/logs
echo "✓ Directories created"

# Check for Notion token
echo ""
echo "→ Checking for Notion API token..."
if security find-generic-password -s "notion-api" -w &>/dev/null; then
    echo "✓ Notion token found in Keychain"
else
    echo "⚠ No Notion token found in Keychain"
    echo ""
    echo "To add your token, run:"
    echo "  security add-generic-password -s \"notion-api\" -a \"\$(whoami)\" -w \"your-token\""
    echo ""
fi

# Initialize database
echo "→ Initializing database..."
python -m notion_sync.database init "$SCRIPT_DIR/memory.db"
echo "✓ Database initialized"

# Summary
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Ensure your Notion API token is in Keychain (see above)"
echo "  2. Test sync: ./bin/notion-sync-now"
echo "  3. Install launchd job:"
echo "     cp com.geoff.notion-sync.plist ~/Library/LaunchAgents/"
echo "     launchctl load ~/Library/LaunchAgents/com.geoff.notion-sync.plist"
echo ""
echo "For help: python -m notion_sync.sync --help"

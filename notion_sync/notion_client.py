"""
Notion API client wrapper for page synchronization.

Provides authenticated access to Notion API for fetching and updating
pages in the knowledge base.
"""

import os
import json
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import logging

try:
    import requests
except ImportError:
    requests = None  # Will use urllib as fallback

logger = logging.getLogger(__name__)


@dataclass
class NotionPage:
    """Represents a Notion page with content and metadata."""
    id: str
    title: str
    parent_id: Optional[str]
    content: str
    last_edited: datetime
    url: str
    properties: Dict[str, Any]


class NotionClientError(Exception):
    """Raised when Notion API operations fail."""
    pass


class NotionClient:
    """
    Client for Notion API interactions.
    
    Handles authentication via macOS Keychain and provides methods
    for reading and writing Notion pages.
    
    Attributes:
        database_id: The Notion database/page ID to sync.
        base_url: Notion API base URL.
        
    Example:
        >>> client = NotionClient(database_id="72d9345fc671480cb9b72d4bd22baf74")
        >>> pages = client.get_updated_pages(since=last_sync_time)
        >>> content = client.get_page_content(page_id)
    """
    
    BASE_URL = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"
    
    def __init__(
        self, 
        database_id: str,
        token: Optional[str] = None,
        keychain_service: str = "notion-api"
    ):
        """
        Initialize Notion client.
        
        Args:
            database_id: Notion database or page ID to sync.
            token: API token. If None, retrieves from macOS Keychain.
            keychain_service: Keychain service name for token lookup.
        """
        self.database_id = self._normalize_id(database_id)
        self._token = token or self._get_token_from_keychain(keychain_service)
        
        if not self._token:
            raise NotionClientError(
                "No Notion API token found. Set NOTION_TOKEN env var "
                "or store in Keychain with service 'notion-api'"
            )
    
    @staticmethod
    def _normalize_id(notion_id: str) -> str:
        """Remove dashes from Notion ID for consistent formatting."""
        return notion_id.replace("-", "")
    
    @staticmethod
    def _format_id(notion_id: str) -> str:
        """Format Notion ID with dashes for API calls."""
        clean = notion_id.replace("-", "")
        if len(clean) == 32:
            return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"
        return notion_id
    
    def _get_token_from_keychain(self, service: str) -> Optional[str]:
        """
        Retrieve Notion API token from macOS Keychain.
        
        Args:
            service: Keychain service name.
            
        Returns:
            Token string if found, None otherwise.
        """
        # First check environment variable
        env_token = os.environ.get("NOTION_TOKEN")
        if env_token:
            return env_token
        
        # Try macOS Keychain
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            logger.warning(f"Could not retrieve token from Keychain service '{service}'")
            return None
        except FileNotFoundError:
            # Not on macOS
            logger.debug("macOS Keychain not available")
            return None
    
    @property
    def _headers(self) -> Dict[str, str]:
        """Request headers for Notion API."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Notion-Version": self.NOTION_VERSION
        }
    
    def _request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make authenticated request to Notion API.
        
        Args:
            method: HTTP method (GET, POST, PATCH).
            endpoint: API endpoint path.
            data: Optional request body.
            
        Returns:
            Parsed JSON response.
            
        Raises:
            NotionClientError: On API errors.
        """
        url = f"{self.BASE_URL}/{endpoint}"
        
        if requests:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers,
                json=data
            )
            if not response.ok:
                raise NotionClientError(
                    f"Notion API error: {response.status_code} - {response.text}"
                )
            return response.json()
        else:
            # Fallback to urllib
            import urllib.request
            import urllib.error
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode() if data else None,
                headers=self._headers,
                method=method
            )
            try:
                with urllib.request.urlopen(req) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                raise NotionClientError(
                    f"Notion API error: {e.code} - {e.read().decode()}"
                )
    
    def get_database_pages(
        self, 
        since: Optional[datetime] = None,
        page_size: int = 100
    ) -> List[NotionPage]:
        """
        Fetch pages from the configured database.
        
        Args:
            since: Only return pages edited after this time.
            page_size: Number of pages per request (max 100).
            
        Returns:
            List of NotionPage objects.
        """
        pages = []
        start_cursor = None
        
        filter_params = {}
        if since:
            filter_params["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": since.isoformat()
                }
            }
        
        while True:
            query_data = {
                "page_size": page_size,
                **filter_params
            }
            if start_cursor:
                query_data["start_cursor"] = start_cursor
            
            response = self._request(
                "POST",
                f"databases/{self._format_id(self.database_id)}/query",
                query_data
            )
            
            for result in response.get("results", []):
                page = self._parse_page(result)
                if page:
                    pages.append(page)
            
            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")
        
        return pages
    
    def get_page(self, page_id: str) -> Optional[NotionPage]:
        """
        Fetch a single page by ID.
        
        Args:
            page_id: Notion page UUID.
            
        Returns:
            NotionPage object or None if not found.
        """
        try:
            response = self._request("GET", f"pages/{self._format_id(page_id)}")
            return self._parse_page(response)
        except NotionClientError as e:
            logger.error(f"Failed to fetch page {page_id}: {e}")
            return None
    
    def get_page_content(self, page_id: str) -> str:
        """
        Fetch full content blocks of a page.
        
        Args:
            page_id: Notion page UUID.
            
        Returns:
            Page content as markdown-formatted string.
        """
        blocks = []
        start_cursor = None
        
        while True:
            endpoint = f"blocks/{self._format_id(page_id)}/children"
            if start_cursor:
                endpoint += f"?start_cursor={start_cursor}"
            
            response = self._request("GET", endpoint)
            blocks.extend(response.get("results", []))
            
            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")
        
        return self._blocks_to_markdown(blocks)
    
    def update_page(
        self, 
        page_id: str, 
        properties: Optional[Dict] = None,
        content: Optional[str] = None
    ) -> bool:
        """
        Update a Notion page.
        
        Args:
            page_id: Notion page UUID.
            properties: Property updates.
            content: New content (replaces existing blocks).
            
        Returns:
            True if successful.
        """
        if properties:
            self._request(
                "PATCH",
                f"pages/{self._format_id(page_id)}",
                {"properties": properties}
            )
        
        if content:
            # Delete existing blocks and add new ones
            # Note: This is a simplified implementation
            logger.warning("Content update not fully implemented - use Notion MCP for complex updates")
        
        return True
    
    def create_page(
        self, 
        title: str, 
        content: str = "",
        properties: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Create a new page in the database.
        
        Args:
            title: Page title.
            content: Initial page content.
            properties: Additional properties.
            
        Returns:
            New page ID if successful, None otherwise.
        """
        page_data = {
            "parent": {"database_id": self._format_id(self.database_id)},
            "properties": {
                "title": {
                    "title": [{"text": {"content": title}}]
                },
                **(properties or {})
            }
        }
        
        try:
            response = self._request("POST", "pages", page_data)
            return response.get("id")
        except NotionClientError as e:
            logger.error(f"Failed to create page: {e}")
            return None
    
    def _parse_page(self, data: Dict) -> Optional[NotionPage]:
        """Parse API response into NotionPage object."""
        try:
            # Extract title from properties
            title = ""
            for prop_name, prop_value in data.get("properties", {}).items():
                if prop_value.get("type") == "title":
                    title_content = prop_value.get("title", [])
                    if title_content:
                        title = title_content[0].get("plain_text", "")
                    break
            
            # Parse parent
            parent = data.get("parent", {})
            parent_id = parent.get("database_id") or parent.get("page_id")
            
            # Parse timestamps
            last_edited_str = data.get("last_edited_time", "")
            last_edited = datetime.fromisoformat(
                last_edited_str.replace("Z", "+00:00")
            ) if last_edited_str else datetime.utcnow()
            
            return NotionPage(
                id=data.get("id", ""),
                title=title,
                parent_id=parent_id,
                content="",  # Fetched separately via get_page_content
                last_edited=last_edited,
                url=data.get("url", ""),
                properties=data.get("properties", {})
            )
        except Exception as e:
            logger.error(f"Failed to parse page data: {e}")
            return None
    
    def _blocks_to_markdown(self, blocks: List[Dict]) -> str:
        """
        Convert Notion blocks to markdown format.
        
        Args:
            blocks: List of Notion block objects.
            
        Returns:
            Markdown-formatted string.
        """
        lines = []
        
        for block in blocks:
            block_type = block.get("type", "")
            block_data = block.get(block_type, {})
            
            text = self._extract_rich_text(block_data.get("rich_text", []))
            
            if block_type == "paragraph":
                lines.append(text)
            elif block_type == "heading_1":
                lines.append(f"# {text}")
            elif block_type == "heading_2":
                lines.append(f"## {text}")
            elif block_type == "heading_3":
                lines.append(f"### {text}")
            elif block_type == "bulleted_list_item":
                lines.append(f"- {text}")
            elif block_type == "numbered_list_item":
                lines.append(f"1. {text}")
            elif block_type == "to_do":
                checked = "x" if block_data.get("checked") else " "
                lines.append(f"- [{checked}] {text}")
            elif block_type == "toggle":
                lines.append(f"<details><summary>{text}</summary></details>")
            elif block_type == "code":
                language = block_data.get("language", "")
                lines.append(f"```{language}\n{text}\n```")
            elif block_type == "quote":
                lines.append(f"> {text}")
            elif block_type == "divider":
                lines.append("---")
            elif block_type == "callout":
                emoji = block_data.get("icon", {}).get("emoji", "💡")
                lines.append(f"> {emoji} {text}")
            else:
                if text:
                    lines.append(text)
        
        return "\n\n".join(lines)
    
    def _extract_rich_text(self, rich_text: List[Dict]) -> str:
        """Extract plain text from Notion rich text array."""
        return "".join(
            item.get("plain_text", "") 
            for item in rich_text
        )


if __name__ == "__main__":
    # Quick test
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m notion_sync.notion_client <database_id>")
        sys.exit(1)
    
    client = NotionClient(database_id=sys.argv[1])
    pages = client.get_database_pages()
    
    print(f"Found {len(pages)} pages:")
    for page in pages[:10]:
        print(f"  - {page.title} ({page.id})")

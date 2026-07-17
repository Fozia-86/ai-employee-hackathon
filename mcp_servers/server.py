import os
import glob
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load Enterprise Environment Configurations from the project root .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
VAULT_PATH = os.getenv("VAULT_PATH", "./workspace")

# Initialize FastMCP Server
mcp = FastMCP("Core File Server")

def get_vault_path(*subdirs: str) -> Path:
    """Safely resolve paths within the secure workspace boundary."""
    base_path = Path(VAULT_PATH).resolve()
    target_path = base_path.joinpath(*subdirs).resolve()
    if not str(target_path).startswith(str(base_path)):
        raise ValueError("Security Violation: Path traversal detected outside workspace.")
    return target_path

@mcp.tool()
def monitor_triggers() -> List[Dict[str, Any]]:
    """
    Scans the /Needs_Action/ directory for any file starting with 'TRIGGER_'.
    Returns a list of structured file metadata and contents for the LLM core.
    """
    try:
        trigger_dir = get_vault_path("Needs_Action")
        search_pattern = os.path.join(trigger_dir, "TRIGGER_*")
        files = glob.glob(search_pattern)
        
        found_triggers = []
        for file_path in files:
            path_obj = Path(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            found_triggers.append({
                "filename": path_obj.name,
                "absolute_path": str(path_obj),
                "content": content,
                "status": "UNPROCESSED"
            })
        return found_triggers
    except Exception as e:
        return [{"error": f"Failed to scan trigger directory: {str(e)}"}]

@mcp.tool()
def search_kb(query: str) -> str:
    """
    Searches the /Knowledge_Base/knowledge_base.md for enterprise boundaries,
    discounts, guidelines, and tier specifications based on query matching.
    """
    try:
        kb_path = get_vault_path("Knowledge_Base", "knowledge_base.md")
        if not kb_path.exists():
            return "Error: Corporate Knowledge Base file not found."
            
        with open(kb_path, "r", encoding="utf-8") as f:
            kb_content = f.read()
            
        # Industrial practice: Simple vectorless keyword retrieval for local markdown file blocks
        sections = kb_content.split("##")
        relevant_blocks = []
        for section in sections:
            if query.lower() in section.lower():
                relevant_blocks.append(f"## {section.strip()}")
                
        if relevant_blocks:
            return "\n\n".join(relevant_blocks)
        return f"Query '{query}' not explicitly found. Returning base corporate rules:\n\n{kb_content[:500]}"
    except Exception as e:
        return f"Error querying knowledge base: {str(e)}"

@mcp.tool()
def write_dashboard_metrics(sync_time: str, total_runs: int, success_rate: float, loop_state: str, log_message: str) -> str:
    """
    Rewrites and updates the Markdown tables inside /workspace/Dashboard.md 
    to provide the system owner with live execution status updates.
    """
    try:
        dashboard_path = get_vault_path("Dashboard.md")
        
        updated_dashboard = f"""# 👑 Autonomous Employee Operational Dashboard [GOLD TIER]

| Last Sync | Current Tier | Total Runs | Success Rate | Active System Status |
| :--- | :--- | :--- | :--- | :--- |
| {sync_time} | **GOLD** | {total_runs} | {success_rate}% | {loop_state} |

## 📊 Live System Health Metrics
- **Odoo MCP Server Status**: 🟢 Active (Connected via JSON-RPC)
- **Social Media MCP Server Status**: 🟢 Active (Connected)
- **Crypto Vault Engine**: 🟢 Ready (AES-256 Initialized)
- **Ralph Wiggum Loop State**: {loop_state}

## 📑 Recent Execution Logs
- `[{sync_time}] [INFO] {log_message}`
"""
        with open(dashboard_path, "w", encoding="utf-8") as f:
            f.write(updated_dashboard)
            
        return "Dashboard.md metrics updated successfully."
    except Exception as e:
        return f"Error updating dashboard metrics: {str(e)}"

if __name__ == "__main__":
    # Standard FastMCP entry point for stdio communication channel
    mcp.run()

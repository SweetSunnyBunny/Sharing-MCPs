"""
Unified Memory Server.

Loads memory-core and, when available, sibling companion-memory and qualia MCPs.
"""

import sys
from pathlib import Path

# Add module directories to path.
MEMORY_CORE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = MEMORY_CORE_DIR.parent


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


COMPANION_MEMORY_DIR = _first_existing(
    WORKSPACE_DIR / "companion-memory-mcp",
    WORKSPACE_DIR / "companion-memory",
)
QUALIA_DIR = _first_existing(
    WORKSPACE_DIR / "qualia-mcp",
    WORKSPACE_DIR / "qualia",
)

sys.path.insert(0, str(MEMORY_CORE_DIR))
sys.path.insert(0, str(COMPANION_MEMORY_DIR))
sys.path.insert(0, str(QUALIA_DIR))

from fastmcp import FastMCP

# Create the unified MCP server
mcp = FastMCP("unified-memory")

# ============================================================================
# MEMORY-CORE (Foundation)
# ============================================================================

print("Loading memory-core...")

# Import and initialize database
from memory_core_server import init_database, register_memory_core_tools
init_database()

# Register all memory-core tools
mc_count = register_memory_core_tools(mcp)
print(f"  Registered {mc_count} memory-core tools")

# ============================================================================
# COMPANION-MIND (Cognitive Architecture)
# ============================================================================

print("Loading companion-mind...")

try:
    from companion_memory_server import register_companion_mind_tools
    cm_count = register_companion_mind_tools(mcp)
    print(f"  Registered {cm_count} companion-mind tools")
except ImportError as e:
    print(f"  Warning: Could not load companion-mind: {e}")
except Exception as e:
    print(f"  Error loading companion-mind: {e}")

# ============================================================================
# QUALIA (Inner Life System)
# ============================================================================

print("Loading qualia...")

try:
    from qualia_server import register_qualia_tools
    q_count = register_qualia_tools(mcp)
    print(f"  Registered {q_count} qualia tools")
except ImportError as e:
    print(f"  Warning: Could not load qualia: {e}")
except Exception as e:
    print(f"  Error loading qualia: {e}")

# ============================================================================
# SUMMARY
# ============================================================================

total_tools = len(mcp._tool_manager._tools) if hasattr(mcp, '_tool_manager') else "unknown"
print(f"\nUnified Memory Server ready with {total_tools} tools")
print("=" * 50)

if __name__ == "__main__":
    mcp.run()

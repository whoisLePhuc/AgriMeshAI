"""AgriMeshAI MCP Server."""
import os
import sys

# Ensure project root is in path for local modules (recorder, config)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

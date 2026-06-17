#!/usr/bin/env python3
"""AgriMeshAI — Unified launcher.

Thin shim that delegates to ``gateway.main``.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(ROOT, "gateway"))
sys.path.insert(0, os.path.join(ROOT, "gateway", "agent"))

from gateway.main import main

if __name__ == "__main__":
    main()

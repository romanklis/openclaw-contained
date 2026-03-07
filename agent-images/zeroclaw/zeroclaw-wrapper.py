#!/usr/bin/env python3
"""
ZeroClaw Adapter — lightweight wrapper around the shared TaskForge adapter.

Sets IMAGE_TYPE=zeroclaw and delegates everything to taskforge-adapter.py.
"""
import os
os.environ.setdefault("OPENCLAW_IMAGE_TYPE", "zeroclaw")

import sys
sys.path.insert(0, "/opt/openclaw")

from importlib.machinery import SourceFileLoader
adapter = SourceFileLoader("adapter", "/opt/openclaw/taskforge-adapter.py").load_module()

if __name__ == "__main__":
    adapter.main()

"""
Configuration for OpenClaw Agent
"""
import os

# Service URLs
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
POLICY_ENGINE_URL = os.getenv("POLICY_ENGINE_URL", "http://policy-engine:8001")

# Task configuration
TASK_ID = os.getenv("TASK_ID")
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "50"))

# Workspace paths
WORKSPACE_ROOT = os.getenv("OPENCLAW_WORKSPACE", "/workspace")
OUTPUT_DIR = os.getenv("OPENCLAW_OUTPUT", "/workspace/output")

# Resource limits (enforced by policy)
MAX_MEMORY = os.getenv("MAX_MEMORY", "4Gi")
MAX_CPU = os.getenv("MAX_CPU", "2")
TIMEOUT = os.getenv("TIMEOUT", "1h")

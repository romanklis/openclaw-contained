# OpenClaw Base Agent Image - Changelog

## [1.0.0] - 2026-02-09

### Added
- Initial base agent image
- Agent runtime with policy enforcement
- Policy client for action validation
- Capability request mechanism
- Workspace isolation
- Non-root user execution (UID 1001)
- Health check endpoint
- Structured logging

### Security
- Non-root user execution
- Policy enforcement at runtime
- Isolated workspace environment
- Network restrictions via policy engine

### Dependencies
- Python 3.11
- httpx 0.27.0
- requests 2.31.0
- pydantic 2.6.0
- structlog 24.1.0

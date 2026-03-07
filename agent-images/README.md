# TaskForge Agent Images

This directory contains version-controlled agent images and the **Agent Profiles
Registry** for the TaskForge platform.

## Architecture

TaskForge supports 4 distinct **Base Images** (the "body") that are paired with
specific LLMs (the "brain") through **Agent Profiles**.

```
agent-images/
├── agent_profiles.yaml      # Agent Profiles Registry (see below)
├── base/                    # OpenClaw — Full Python environment
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── agent_runtime.py
│   ├── policy_client.py
│   ├── config.py
│   ├── VERSION
│   └── CHANGELOG.md
├── nanobot/                 # NanoBot — Lightweight Alpine Python
│   ├── Dockerfile
│   └── requirements.txt
├── picoclaw/                # PicoClaw — Minimal Shell (Alpine)
│   ├── Dockerfile
│   ├── agent_runner.sh
│   └── policy_check.sh
├── zeroclaw/                # ZeroClaw — Rust-based runtime
│   ├── Dockerfile
│   └── agent/
│       ├── Cargo.toml
│       └── src/main.rs
└── README.md
```

## Base Images

| Image      | Runtime             | Use Case                               | Size     |
|------------|---------------------|----------------------------------------|----------|
| **OpenClaw**  | Python 3.11 (Debian) | Full coding & automation              | ~450 MB  |
| **NanoBot**   | Python 3.11 (Alpine) | Fast scripts & data transforms        | ~80 MB   |
| **PicoClaw**  | Shell (Alpine)       | File manipulation & CLI automation    | ~15 MB   |
| **ZeroClaw**  | Rust (Debian)        | High-security & performance-critical  | ~120 MB  |

## Agent Profiles

Agent Profiles abstract LLM selection away from users. Instead of picking a raw
model name, users select a **profile** which is a pre-defined combination of:

- **Base Image** (Docker tag) — the execution environment
- **LLM Model** (API name) — the AI model powering the agent

See [`agent_profiles.yaml`](agent_profiles.yaml) for the full registry.

### Example

When a user selects **"Senior Reviewer"**, the system resolves:
- **Body**: `openclaw-agent:zeroclaw` (Rust runtime)
- **Brain**: `claude-sonnet-4-20250514`

The UI shows badges: `Runtime: Rust (Debian)` · `Model: claude-sonnet-4-20250514`

## Building Images

```bash
# Build all 4 base images
make build-all-images

# Or individually:
make build-base       # OpenClaw (full Python)
make build-nanobot    # NanoBot (Alpine Python)
make build-picoclaw   # PicoClaw (Shell)
make build-zeroclaw   # ZeroClaw (Rust)
```

# Task-specific metadata
LABEL task_id="task-abc123"
LABEL capabilities="pip_package:pandas"
```

## Version Control

Each image version is tracked with:

1. **VERSION file**: Semver version number
2. **CHANGELOG.md**: Detailed changes
3. **Git tags**: `agent-base-v1.0.0`

### Versioning Strategy

- **Major**: Breaking changes to runtime API
- **Minor**: New features, capability additions
- **Patch**: Bug fixes, security updates

## Building Custom Variants

You can create pre-approved image variants for common use cases:

```bash
# Example: Data science variant
cd agent-images/variants/data-science
docker build -t openclaw-agent-ds:1.0.0 .
```

## Registry

Images are stored in the local registry at `localhost:5000`:

```bash
# Tag and push
docker tag openclaw-base:1.0.0 localhost:5000/openclaw-base:1.0.0
docker push localhost:5000/openclaw-base:1.0.0
```

## Security Considerations

1. **Least Privilege**: Base image has minimal permissions
2. **No Root**: Agent runs as UID 1001
3. **Policy Enforced**: All actions validated at runtime
4. **Network Isolation**: Controlled via Docker networks
5. **Resource Limits**: Enforced via Docker and policy

## Development Workflow

1. **Modify** base image sources
2. **Update** VERSION and CHANGELOG
3. **Build** and test locally
4. **Tag** in git: `git tag agent-base-v1.0.1`
5. **Push** to registry
6. **Update** image-builder default base image

## Testing

Test the agent runtime:

```bash
docker run --rm \
  -e TASK_ID=test-123 \
  -e CONTROL_PLANE_URL=http://control-plane:8000 \
  openclaw-base:1.0.0
```

## Future Enhancements

- [ ] Multi-language support (Node.js, Go)
- [ ] GPU-enabled variants
- [ ] Signed images (Cosign)
- [ ] SBOM generation
- [ ] Vulnerability scanning integration

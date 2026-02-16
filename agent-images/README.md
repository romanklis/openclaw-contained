# TaskForge Agent Images

This directory contains version-controlled agent images for the TaskForge platform.

## Structure

```
agent-images/
├── base/                    # Base agent image (foundation)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── agent_runtime.py
│   ├── policy_client.py
│   ├── config.py
│   ├── VERSION
│   └── CHANGELOG.md
├── variants/                # Pre-built variants (future)
│   ├── data-science/
│   ├── web-scraping/
│   └── code-analysis/
└── README.md
```

## Base Image

The base image (`openclaw-base:1.0.0`) provides:

- **Runtime**: Python 3.11 with core dependencies
- **Security**: Non-root execution (UID 1001)
- **Policy Integration**: Built-in policy client
- **Workspace**: Isolated `/workspace` directory
- **Monitoring**: Health checks and structured logging

### Building the Base Image

```bash
cd agent-images/base
docker build -t openclaw-base:1.0.0 .
docker tag openclaw-base:1.0.0 openclaw-base:latest
```

### Using the Base Image

The image-builder service uses this as the foundation and layers approved capabilities on top:

```dockerfile
FROM openclaw-base:1.0.0

# Install approved capability
RUN pip install pandas==2.0.0

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

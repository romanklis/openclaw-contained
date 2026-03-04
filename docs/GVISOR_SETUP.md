gVisor is an application kernel that provides an additional layer of isolation between containers and the host OS. It intercepts system calls from containerised applications and services them in user-space, significantly reducing the attack surface of the host kernel.
OpenClaw uses gVisor to sandbox agent containers.
# Installing gVisor (runsc) on Ubuntu in WSL2

## Why container isolation matters

Running untrusted or third-party code inside containers is common in modern cloud and AI workflows. However, traditional container runtimes (like runc) share the host kernel with all containers, which means a vulnerability in the kernel or container escape exploit can compromise the entire system.

gVisor addresses this by acting as a lightweight user-space kernel between your containers and the host. It intercepts and emulates most system calls, dramatically reducing the risk that a compromised container can affect the host or other workloads. This is especially important for platforms like OpenClaw, which execute dynamic, user-supplied, or AI-generated code in multi-tenant environments.

**Key benefits of using gVisor:**
- Stronger isolation for agent and task containers
- Mitigates kernel zero-day and container escape risks
- No need for heavyweight virtual machines
- Minimal changes to your Docker workflow

OpenClaw uses gVisor to sandbox agent containers for improved security and peace of mind.

## Prerequisites

| Requirement | Minimum version |
|-------------|-----------------|
| Windows 10/11 with WSL2 | WSL kernel ≥ 5.10 |
| Ubuntu | 22.04 LTS (Jammy) or later |
| Docker Engine | 20.10+ (installed inside WSL) |
| Architecture | x86_64 (amd64) |

Verify you are running inside WSL2:

```bash
uname -r
# Should contain "microsoft" and "WSL2", e.g. 5.15.167.4-microsoft-standard-WSL2
```

## Step 1 — Install dependencies

```bash
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg
```

## Step 2 — Add the gVisor apt repository

Import the signing key:

```bash
curl -fsSL https://gvisor.dev/archive.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
```

Add the repository:

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] \
  https://storage.googleapis.com/gvisor/releases release main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
```

## Step 3 — Install runsc

```bash
sudo apt-get update
sudo apt-get install -y runsc
```

Verify the installation:

```bash
runsc --version
# runsc version release-20260223.0
# spec: 1.1.0-rc.1
```

## Step 4 — Register runsc as a Docker runtime

The `runsc install` command automatically adds gVisor to `/etc/docker/daemon.json`:

```bash
sudo runsc install
```

This creates or updates the daemon config:

```json
{
    "runtimes": {
        "runsc": {
            "path": "/usr/bin/runsc"
        }
    }
}
```

> **Note:** If you already have a custom `/etc/docker/daemon.json` (for example with `insecure-registries`), `runsc install` will merge the runtime entry into it. Verify the result with `cat /etc/docker/daemon.json`.

## Step 5 — Restart Docker

```bash
sudo systemctl restart docker
```

## Step 6 — Smoke test

```bash
docker run --rm --runtime=runsc hello-world
```

You should see the normal "Hello from Docker!" output. If it succeeds, gVisor is working correctly.

To confirm the container is actually using the gVisor kernel:

```bash
docker run --rm --runtime=runsc ubuntu:22.04 dmesg | head -3
# [    0.000000] Starting gVisor...
```

## Using gVisor with Docker Compose

In a `docker-compose.yml` file, specify the runtime on the service:

```yaml
services:
  my-sandboxed-service:
    image: my-image:latest
    runtime: runsc
```

## Enabling gVisor in OpenClaw

OpenClaw uses the `AGENT_SANDBOX_MODE` environment variable to control how agent containers are launched. After installing gVisor on the host, flip the mode to `gvisor` in your `.env` file:

```bash
# Copy the example if you don't have a .env yet
cp .env.example .env
```

Then edit `.env`:

```dotenv
AGENT_SANDBOX_MODE=gvisor
```

### Available modes

| Mode | Runtime | `privileged` | Security | Use case |
|------|---------|-------------|----------|----------|
| `gvisor` | `runsc` | `false` | ✅ Strong | Production / any shared host |
| `insecure-dind` | default (runc) | `true` | ⚠️ Weak | Local development only |

### What happens at startup

- **`make up`** reads `.env` and prints a **red terminal warning** if the mode is `insecure-dind`, or a green confirmation if `gvisor` is active.
- The **Temporal Worker** logs the sandbox mode at boot and again on every container launch.
- If `gvisor` is selected but `runsc` is not registered with Docker, the worker logs a clear error message pointing to this document.

### How it flows through the stack

```
.env
 └─▶ docker-compose.yml   (AGENT_SANDBOX_MODE env var)
      └─▶ temporal-worker  (reads os.getenv)
           └─▶ docker SDK  (runtime="runsc" / privileged=True)
```

### Agents that need to build images

gVisor intentionally blocks the privileged syscalls required to run a nested Docker daemon. If an agent needs to build container images, use **daemonless builders** inside the sandbox instead:

- **[Kaniko](https://github.com/GoogleContainerTools/kaniko)** — builds images in user-space without Docker.
- **[Buildah](https://github.com/containers/buildah)** — OCI image builder, rootless-capable.

Install either tool in the agent's base image; both are fully compatible with gVisor.

### Multi-agent data exchange

Sharing a single writable host volume across multiple gVisor containers can incur I/O overhead due to the Gofer protocol. For high-concurrency meeting points, prefer exchanging data **through the orchestrator**:

1. Agent A writes its result to stdout / sends it to the FastAPI backend.
2. The Temporal Workflow picks up the result.
3. The Workflow passes the data as an input parameter to Agent B.

## WSL-specific notes

1. **No nested KVM.** WSL2 does not expose `/dev/kvm`, so gVisor runs in its default **ptrace** platform mode. This works out of the box — no extra flags needed.

2. **systemd.** If `systemctl` is not available in your WSL distro, enable systemd by adding the following to `/etc/wsl.conf` and restarting WSL (`wsl --shutdown` from PowerShell):

   ```ini
   [boot]
   systemd=true
   ```

3. **Memory.** gVisor adds modest overhead. If you run many sandboxed containers, consider increasing the WSL2 memory limit in `%USERPROFILE%\.wslconfig`:

   ```ini
   [wsl2]
   memory=16GB
   ```

4. **File system performance.** For best I/O performance, keep project files inside the Linux file system (`/home/...`) rather than on a Windows mount (`/mnt/c/...`).

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `docker: Error response from daemon: unknown or invalid runtime name: runsc` | Run `sudo runsc install` and `sudo systemctl restart docker` |
| `runsc: command not found` | Verify the apt repo was added correctly and re-run `sudo apt-get update && sudo apt-get install -y runsc` |
| `permission denied` on `/var/run/docker.sock` | Add your user to the docker group: `sudo usermod -aG docker $USER` and start a new shell |
| Containers crash with `FATAL: ptrace` errors | Ensure your WSL2 kernel is ≥ 5.10 (`uname -r`). Update WSL with `wsl --update` from PowerShell |

## References

- [gVisor documentation](https://gvisor.dev/docs/)
- [gVisor Docker quick start](https://gvisor.dev/docs/user_guide/docker/)
- [WSL2 systemd support](https://learn.microsoft.com/en-us/windows/wsl/systemd)

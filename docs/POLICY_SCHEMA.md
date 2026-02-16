# Policy Schema Reference

Policies define what capabilities an agent has within a task. Each task has a versioned
policy chain — when a new capability is approved, a new policy version is created.

## Database Model

Policies are stored in the `policies` table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `task_id` | String | Foreign key → `tasks.id` |
| `version` | Integer | Auto-incrementing per task |
| `tools_allowed` | JSON | List of allowed tool names |
| `network_rules` | JSON | Network access configuration |
| `filesystem_rules` | JSON | Filesystem access rules |
| `database_rules` | JSON | Database access rules |
| `resource_limits` | JSON | CPU, memory, timeout limits |
| `created_at` | DateTime | Creation timestamp |
| `created_by` | String | Who created this version |

## API

### List Policies

```
GET /api/policies?task_id=task-abc123
```

### Get Policy by ID

```
GET /api/policies/{policy_id}
```

### Get Current Policy for a Task

```
GET /api/policies/task/{task_id}/current
```

Returns the latest version of the policy for the given task.

### Create New Policy Version

```
POST /api/policies
Content-Type: application/json

{
  "task_id": "task-abc123",
  "rules": {
    "tools_allowed": ["python3", "curl"],
    "network_rules": {
      "allow_outbound": true,
      "whitelist": ["api.example.com"]
    },
    "filesystem_rules": {
      "writable_paths": ["/workspace", "/workspace/output"]
    },
    "database_rules": {},
    "resource_limits": {
      "max_memory_mb": 512,
      "timeout_seconds": 3600
    }
  }
}
```

The version number is auto-incremented based on the latest existing version for that task.

## Policy Fields

### `tools_allowed`

A list of tool/command names the agent is permitted to use.

```json
{
  "tools_allowed": ["python3", "pip", "git", "curl"]
}
```

Default: `[]` (no tools beyond the base image).

### `network_rules`

Controls network access for the agent container.

```json
{
  "network_rules": {
    "allow_outbound": false,
    "whitelist": []
  }
}
```

Default: `{}` (no network access).

> **Note:** Network rules are stored in the policy but enforcement at the container
> level (firewall/iptables) is not yet implemented. Currently, agent containers
> inherit the DinD network configuration.

### `filesystem_rules`

Controls filesystem access paths.

```json
{
  "filesystem_rules": {
    "writable_paths": ["/workspace", "/workspace/output"],
    "readable_paths": ["/workspace/input"],
    "forbidden_paths": ["/etc", "/root"]
  }
}
```

Default: `{}` (workspace directory is always available).

> **Note:** Filesystem rules are stored in the policy but fine-grained enforcement
> (read-only mounts, seccomp) is not yet implemented.

### `database_rules`

Controls database access for agents.

```json
{
  "database_rules": {
    "enabled": false,
    "allowed_tables": [],
    "max_query_time": "30s"
  }
}
```

Default: `{}` (no database access).

> **Note:** Database proxy is not yet implemented. This field is reserved for future use.

### `resource_limits`

CPU, memory, and timeout limits for agent containers.

```json
{
  "resource_limits": {
    "max_cpu": "2",
    "max_memory_mb": 4096,
    "timeout_seconds": 3600
  }
}
```

Default: `{}` (Docker defaults apply).

## How Policies Evolve

1. **Task created** → policy version 1 (minimal defaults)
2. **Agent requests capability** → workflow pauses
3. **Human approves** → new policy version created with the added capability
4. **Image builder** rebuilds the agent container image with approved packages
5. **Workflow resumes** with the new image and updated policy

Each policy version is immutable. The full version history is preserved for audit.

## Example: Policy Evolution

```
Version 1 (task creation):
  tools_allowed: []
  network_rules: {}

Version 2 (approved pandas):
  tools_allowed: ["pip:pandas"]
  network_rules: {}

Version 3 (approved network for API access):
  tools_allowed: ["pip:pandas", "pip:requests"]
  network_rules: {"allow_outbound": true, "whitelist": ["api.example.com"]}
```

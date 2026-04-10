"""
Pydantic schemas for API
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum


# Task schemas
class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    name: str
    description: Optional[str] = None
    prompt: Optional[str] = None  # alias for description (from curl/CLI)
    input_data: Optional[Dict[str, Any]] = None
    initial_policy: Optional[Dict[str, Any]] = None
    llm_model: Optional[str] = None
    model: Optional[str] = None  # alias for llm_model (from curl/CLI)
    base_image: Optional[str] = None  # agent base image key (e.g. "zeroclaw")
    agent_profile: Optional[str] = None  # agent profile ID for display
    # ── DAG context (optional) ──
    workspace_id: Optional[str] = None   # share an existing workspace
    dag_id: Optional[str] = None         # link to parent DAG
    node_id: Optional[str] = None        # DAG node ID
    auto_start: bool = True              # set False to create without starting

    @property
    def effective_description(self) -> Optional[str]:
        """Return description or prompt (whichever is set)."""
        return self.description or self.prompt

    @property
    def effective_model(self) -> str:
        """Return llm_model or model (whichever is set), defaulting to gemma3:4b."""
        return self.llm_model or self.model or "gemma3:4b"

    @property
    def effective_base_image_tag(self) -> str:
        """Return the full registry image tag for the selected base image."""
        key = self.base_image or "openclaw"
        return f"localhost:5000/openclaw-agent:{key}"


class TaskResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: TaskStatus
    workspace_id: str
    workflow_id: Optional[str]
    agent_profile: Optional[str] = None
    dag_id: Optional[str] = None
    node_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class TaskDetail(TaskResponse):
    current_image: Optional[str]
    current_policy_id: Optional[int]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    llm_model: Optional[str]


class TaskContinue(BaseModel):
    """Follow-up instructions to continue iterating on a completed/failed task."""
    follow_up: str = Field(..., min_length=1, description="Follow-up instructions for the agent")
    llm_model: Optional[str] = None  # Override model if desired
    

# Capability schemas
class CapabilityType(str, Enum):
    TOOL_INSTALL = "tool_install"
    NETWORK_ACCESS = "network_access"
    FILESYSTEM_ACCESS = "filesystem_access"
    DATABASE_ACCESS = "database_access"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    MODIFIED = "modified"


class CapabilityRequestCreate(BaseModel):
    task_id: str
    capability_type: CapabilityType
    resource_name: str
    justification: str
    details: Optional[Dict[str, Any]] = None


class CapabilityRequestResponse(BaseModel):
    id: int
    task_id: str
    capability_type: CapabilityType
    resource_name: str
    justification: str
    status: RequestStatus
    requested_at: datetime
    decided_at: Optional[datetime]
    decided_by: Optional[str]
    decision_notes: Optional[str]
    details: Optional[Dict[str, Any]] = None
    alternative_suggestion: Optional[str] = None
    
    class Config:
        from_attributes = True


class CapabilityDecision(BaseModel):
    request_id: Optional[int] = None  # For legacy endpoint
    approved: Optional[bool] = None  # For legacy endpoint
    decision: Optional[str] = None  # approved, denied, alternative_suggested
    notes: Optional[str] = None
    comment: Optional[str] = None
    alternative_suggestion: Optional[str] = None
    reviewed_by: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None


# Policy schemas
class PolicyRules(BaseModel):
    tools_allowed: List[str] = []
    network_rules: Dict[str, Any] = {}
    filesystem_rules: Dict[str, Any] = {}
    database_rules: Dict[str, Any] = {}
    resource_limits: Dict[str, Any] = {}


class PolicyCreate(BaseModel):
    task_id: str
    rules: PolicyRules


class PolicyResponse(BaseModel):
    id: int
    task_id: str
    version: int
    tools_allowed: List[str]
    network_rules: Dict[str, Any]
    filesystem_rules: Dict[str, Any]
    database_rules: Dict[str, Any]
    resource_limits: Dict[str, Any]
    created_at: datetime
    
    class Config:
        from_attributes = True


# Task output schemas
class TaskOutputCreate(BaseModel):
    task_id: str
    iteration: int
    completed: Optional[str] = "false"
    capability_requested: Optional[str] = "false"
    agent_logs: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    llm_response_preview: Optional[str] = None
    model_used: Optional[str] = None
    image_used: Optional[str] = None
    duration_ms: Optional[int] = None
    deliverables: Optional[Dict[str, str]] = None
    raw_result: Optional[Dict[str, Any]] = None


class TaskOutputResponse(BaseModel):
    id: int
    task_id: str
    iteration: int
    completed: Optional[str]
    capability_requested: Optional[str]
    agent_logs: Optional[str]
    output: Optional[str]
    error: Optional[str]
    llm_response_preview: Optional[str]
    model_used: Optional[str]
    image_used: Optional[str]
    duration_ms: Optional[int]
    deliverables: Optional[Dict[str, str]]
    raw_result: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


# Task message schemas
class TaskMessageCreate(BaseModel):
    content: str
    role: Optional[str] = "user"
    metadata: Optional[Dict[str, Any]] = None


class TaskMessageResponse(BaseModel):
    id: int
    task_id: str
    role: str
    content: str
    metadata: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


# Auth schemas
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class User(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None


# Deployment schemas
class DeploymentStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    BUILDING = "building"
    BUILT = "built"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class DeploymentRequestCreate(BaseModel):
    """Agent requests a deployment (from wrapper)"""
    task_id: str
    name: str
    entrypoint: str  # e.g. "python app.py"
    port: int = 5000
    files: Optional[Dict[str, str]] = None  # workspace files snapshot
    agent_image: Optional[str] = None  # agent's committed image to use as deploy base


class DeploymentResponse(BaseModel):
    id: str
    name: str
    task_id: str
    image_tag: Optional[str]
    agent_image: Optional[str]
    entrypoint: Optional[str]
    port: Optional[int]
    status: DeploymentStatus
    container_id: Optional[str]
    host_port: Optional[int]
    url: Optional[str]
    created_at: datetime
    approved_at: Optional[datetime]
    built_at: Optional[datetime]
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    error: Optional[str]

    class Config:
        from_attributes = True


class DeploymentDecision(BaseModel):
    approved: bool
    notes: Optional[str] = None


# SBOM schemas

class SBOMPackage(BaseModel):
    """Single package entry inside an SBOM."""
    name: str
    version: Optional[str] = None
    type: Optional[str] = None  # pip, apt, npm, go, etc.
    license: Optional[str] = None


class SBOMResponse(BaseModel):
    """Response for a single SBOM document."""
    id: int
    task_id: str
    image_tag: str
    image_version: int
    format: str
    packages: List[SBOMPackage]
    generator: Optional[str] = None
    generated_at: datetime

    class Config:
        from_attributes = True


class SBOMDetailResponse(SBOMResponse):
    """Full SBOM including the raw SPDX / CycloneDX document."""
    document: Dict[str, Any]


class SBOMSearchResult(BaseModel):
    """One hit when searching SBOMs for a specific package."""
    sbom_id: int
    task_id: str
    image_tag: str
    image_version: int
    package_name: str
    package_version: Optional[str] = None
    package_type: Optional[str] = None
    package_license: Optional[str] = None
    generated_at: datetime


class SBOMDiffEntry(BaseModel):
    """One change between two SBOM versions."""
    change: str  # added, removed, changed
    name: str
    type: Optional[str] = None
    old_version: Optional[str] = None
    new_version: Optional[str] = None


class SBOMDiffResponse(BaseModel):
    """Diff between two SBOM versions of the same task."""
    task_id: str
    from_version: int
    to_version: int
    changes: List[SBOMDiffEntry]


# =========================================================================
# DAG Orchestration schemas — Task-Centric Skill-Based Execution
# =========================================================================

class DAGStatus(str, Enum):
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExecutionEnvironment(str, Enum):
    DIND = "dind"
    DEDICATED_VM = "dedicated_vm"


# ── Skills ──────────────────────────────────────────────

class SkillStepCreate(BaseModel):
    """A single step within a skill."""
    step_id: str
    name: str
    description: Optional[str] = None
    base_image: Optional[str] = None  # override for this step
    tool_hints: Optional[List[str]] = None  # suggested tools


class SkillCreate(BaseModel):
    """Create a reusable skill template."""
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, str] = {}  # {key: type_description}
    output_artifacts: List[str] = []  # expected output file paths
    steps: List[SkillStepCreate] = []
    tags: List[str] = []


class SkillUpdate(BaseModel):
    """Update a skill (bumps version)."""
    description: Optional[str] = None
    input_schema: Optional[Dict[str, str]] = None
    output_artifacts: Optional[List[str]] = None
    steps: Optional[List[SkillStepCreate]] = None
    tags: Optional[List[str]] = None


class SkillResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    version: int
    input_schema: Dict[str, Any]
    output_artifacts: List[str]
    steps: List[Dict[str, Any]]
    tags: List[str]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── DAG Nodes ───────────────────────────────────────────

class DAGNodeCreate(BaseModel):
    """A node in the Master DAG."""
    node_id: str
    skill_id: Optional[str] = None
    skill_step_index: Optional[int] = None
    description: Optional[str] = None
    depends_on: List[str] = []
    config: Dict[str, Any] = {}  # base_image, llm_model, env_id, timeout_minutes, deploy_authorized
    input_mapping: Dict[str, Any] = {}  # supports dependency refs and literal constants


class DAGNodeResponse(BaseModel):
    id: int
    dag_id: str
    node_id: str
    skill_id: Optional[str]
    skill_step_index: Optional[int]
    description: Optional[str]
    status: NodeStatus
    depends_on: List[str]
    config: Dict[str, Any]
    input_mapping: Dict[str, Any]
    output_data: Optional[Dict[str, Any]]
    task_id: Optional[str]
    container_id: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class DAGEdge(BaseModel):
    """A conditional edge in the DAG (e.g. rework loops)."""
    from_node: str  # source node_id
    to_node: str  # target node_id
    condition: str  # e.g. "review-4.verdict == 'FAIL'"
    edge_type: str = "rework"  # rework, skip, etc.


# ── Master DAG ──────────────────────────────────────────

class DAGCreate(BaseModel):
    """Create a new DAG from an objective (invokes the Planner)."""
    objective: str
    llm_model: Optional[str] = None
    base_image: Optional[str] = None
    auto_start: bool = False


class DAGManualCreate(BaseModel):
    """Create a DAG with an explicit node graph (skip planner)."""
    objective: str
    nodes: List[DAGNodeCreate]
    edges: List[DAGEdge] = []
    default_image: str = "openclaw"
    default_llm: str = "gemma3:4b"


class DAGResponse(BaseModel):
    id: str
    objective: str
    status: DAGStatus
    workspace_id: str
    llm_model: Optional[str]
    workflow_id: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class DAGDetail(DAGResponse):
    """Full DAG detail including nodes and edges."""
    dag_json: Dict[str, Any]
    nodes: List[DAGNodeResponse] = []


# ── Node Environments ──────────────────────────────────

class NodeEnvironmentCreate(BaseModel):
    """Create a reusable execution environment."""
    name: str
    description: Optional[str] = None
    base_image: str = "openclaw"
    capabilities: List[str] = []  # initial packages/tools


class NodeEnvironmentFork(BaseModel):
    """Fork an existing environment."""
    name: str
    description: Optional[str] = None


class NodeEnvironmentResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    capability_fingerprint: Optional[str]
    capabilities: List[str]
    base_image: str
    current_image_tag: Optional[str]
    version: int
    parent_env_id: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# =========================================================================
# Supply Chain schemas — Package allowlist management
# =========================================================================

class SupplyChainPackageCreate(BaseModel):
    """Add a package to the supply-chain allowlist."""
    image_type: str
    manager: str  # pip, apt, apk, npm
    package_name: str
    notes: Optional[str] = None
    is_exception: bool = False


class SupplyChainPackageResponse(BaseModel):
    id: int
    image_type: str
    manager: str
    package_name: str
    notes: Optional[str]
    is_exception: str
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class SupplyChainBulkAdd(BaseModel):
    """Add multiple packages at once."""
    image_type: str
    manager: str
    packages: List[str]
    notes: Optional[str] = None
    is_exception: bool = False


class SupplyChainAliasCreate(BaseModel):
    """Add a cross-distro alias mapping."""
    direction: str  # apt_to_apk or apk_to_apt
    from_name: str
    to_name: str


class SupplyChainAliasResponse(BaseModel):
    id: int
    direction: str
    from_name: str
    to_name: str
    created_at: datetime

    class Config:
        from_attributes = True


class SupplyChainImageTypeCreate(BaseModel):
    """Create/update an image type."""
    image_type: str
    notes: Optional[str] = None


class SupplyChainImageTypeResponse(BaseModel):
    id: int
    image_type: str
    notes: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class SupplyChainImageTypeSummary(BaseModel):
    """Summary of an image type with package counts."""
    image_type: str
    notes: Optional[str]
    pip: int = 0
    apt: int = 0
    apk: int = 0
    npm: int = 0
    exceptions: int = 0


class SupplyChainFullConfig(BaseModel):
    """Full supply-chain config (mirrors the YAML structure)."""
    image_types: List[SupplyChainImageTypeSummary]
    aliases: Dict[str, Dict[str, str]]
    raw: Dict[str, Any]
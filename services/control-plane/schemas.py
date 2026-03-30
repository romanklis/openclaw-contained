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
    # ── rework / task-force context (optional) ──
    workspace_id: Optional[str] = None   # share an existing workspace
    task_force_id: Optional[str] = None  # link to parent task force
    task_force_role: Optional[str] = None
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
    task_force_id: Optional[str] = None
    task_force_role: Optional[str] = None
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


class DeploymentResponse(BaseModel):
    id: str
    name: str
    task_id: str
    image_tag: Optional[str]
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
# Task Force schemas — Multi-Agent Orchestration
# =========================================================================

class TaskForceStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CeremonyType(str, Enum):
    PLANNING = "planning"
    SYNC = "sync"
    PEER_REVIEW = "peer_review"
    REVIEW_GATE = "review_gate"
    AGGREGATION = "aggregation"
    CUSTOM = "custom"


class CeremonyMode(str, Enum):
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"


class ExecutionEnvironment(str, Enum):
    DIND = "dind"
    DEDICATED_VM = "dedicated_vm"


# ── Members ─────────────────────────────────────────────

class TaskForceMemberCreate(BaseModel):
    """Define a member of the Task Force."""
    agent_profile: str  # profile ID
    role: str  # e.g. "Researcher", "Developer"
    responsibilities: Optional[str] = None
    llm_model: Optional[str] = None  # override
    base_image: Optional[str] = None  # override
    execution_order: int = 0


class TaskForceMemberResponse(BaseModel):
    id: int
    task_force_id: str
    agent_profile: str
    role: str
    responsibilities: Optional[str]
    llm_model: Optional[str]
    base_image: Optional[str]
    task_id: Optional[str]
    status: str
    execution_order: int
    created_at: datetime

    class Config:
        from_attributes = True


# ── Ceremonies ──────────────────────────────────────────

class TaskForceCeremonyCreate(BaseModel):
    """Define a coordination ceremony.

    For ``review_gate`` ceremonies, set ``review_target_order`` to the
    execution_order that should be re-run when the reviewer verdict is FAIL.
    The workflow reads the reviewer's verdict file from the workspace,
    creates fresh tasks for the target order-groups, and re-executes them.
    Use ``max_rework_cycles`` to cap the number of feedback iterations.
    """
    name: str
    ceremony_type: CeremonyType
    mode: CeremonyMode = CeremonyMode.SYNCHRONOUS
    sequence_order: int = 0
    participant_member_ids: Optional[List[int]] = None  # null = all members
    description: Optional[str] = None
    trigger_condition: str = "after_all_complete"  # after_all_complete, manual
    timeout_minutes: int = 60
    # ── review_gate specific ──
    review_target_order: Optional[int] = None   # execution_order to rewind to on FAIL
    max_rework_cycles: int = 2                  # max feedback loops (0 = unlimited)
    verdict_file: str = "REVIEW_BRIEF.md"       # workspace file containing PASS/FAIL


class TaskForceCeremonyResponse(BaseModel):
    id: int
    task_force_id: str
    name: str
    ceremony_type: CeremonyType
    mode: CeremonyMode
    sequence_order: int
    participant_member_ids: Optional[List[int]]
    description: Optional[str]
    trigger_condition: Optional[str]
    review_target_order: Optional[int] = None
    max_rework_cycles: int = 2
    verdict_file: str = "REVIEW_BRIEF.md"
    timeout_minutes: int
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    result_summary: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Task Force CRUD ─────────────────────────────────────

class TaskForceCreate(BaseModel):
    """Create a Task Force with members and ceremonies."""
    name: str
    description: Optional[str] = None
    objective: str
    execution_environment: ExecutionEnvironment = ExecutionEnvironment.DIND
    members: List[TaskForceMemberCreate]
    ceremonies: List[TaskForceCeremonyCreate] = []


class TaskForceResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    objective: str
    execution_environment: ExecutionEnvironment
    status: TaskForceStatus
    workspace_id: str
    workflow_id: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class TaskForceDetail(TaskForceResponse):
    """Full Task Force detail including members and ceremonies."""
    members: List[TaskForceMemberResponse] = []
    ceremonies: List[TaskForceCeremonyResponse] = []


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


# =========================================================================
# Ceremony State schemas — artifacts, verdicts & agent state exchange
# =========================================================================

class ArtifactKind(str, Enum):
    PLAN = "plan"
    REVIEW_BRIEF = "review_brief"
    VERDICT = "verdict"
    SUMMARY = "summary"
    SYNC_NOTES = "sync_notes"
    REWORK_FEEDBACK = "rework_feedback"
    CUSTOM = "custom"


# ── Ceremony Artifacts ──────────────────────────────────

class CeremonyArtifactCreate(BaseModel):
    """Create a ceremony artifact (immutable once created)."""
    kind: ArtifactKind
    ceremony_id: Optional[int] = None
    task_id: Optional[str] = None  # producing agent's task_id
    filename: Optional[str] = None
    title: Optional[str] = None
    content: str
    metadata: Optional[Dict[str, Any]] = None
    verdict: Optional[str] = None  # "pass" / "fail" — only for verdict artifacts
    rework_cycle: int = 0


class CeremonyArtifactResponse(BaseModel):
    id: int
    task_force_id: str
    ceremony_id: Optional[int]
    task_id: Optional[str]
    kind: ArtifactKind
    filename: Optional[str]
    title: Optional[str]
    content: str
    metadata: Optional[Dict[str, Any]] = Field(None, alias="metadata_json")
    verdict: Optional[str]
    rework_cycle: int
    superseded_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


# ── Verdicts (convenience wrapper over artifact) ────────

class VerdictSubmit(BaseModel):
    """Submit a verdict for a task (immutable once created).

    Used by agents (via the LLM router proxy) or by the review_gate
    ceremony handler.
    """
    verdict: str  # "pass" or "fail"
    summary: Optional[str] = None
    files_reviewed: Optional[List[str]] = None
    ceremony_id: Optional[int] = None
    rework_cycle: int = 0


class VerdictResponse(BaseModel):
    id: int
    task_force_id: str
    task_id: str
    verdict: str
    summary: Optional[str]
    files_reviewed: Optional[List[str]]
    rework_cycle: int
    created_at: datetime

    class Config:
        from_attributes = True


# ── Agent State Exchange ────────────────────────────────

class AgentStateExchangeCreate(BaseModel):
    """Post a state message to the task force channel."""
    from_task_id: str
    to_task_id: Optional[str] = None  # null = broadcast to all
    state_type: str  # "status_update", "decision", "handoff", "feedback"
    subject: Optional[str] = None
    body: Optional[str] = None
    state_data: Optional[Dict[str, Any]] = None


class AgentStateExchangeResponse(BaseModel):
    id: int
    task_force_id: str
    from_task_id: str
    to_task_id: Optional[str]
    state_type: str
    subject: Optional[str]
    body: Optional[str]
    state_data: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True
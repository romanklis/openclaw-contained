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

    @property
    def effective_description(self) -> Optional[str]:
        """Return description or prompt (whichever is set)."""
        return self.description or self.prompt

    @property
    def effective_model(self) -> str:
        """Return llm_model or model (whichever is set), defaulting to gemma3:4b."""
        return self.llm_model or self.model or "gemma3:4b"


class TaskResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: TaskStatus
    workspace_id: str
    workflow_id: Optional[str]
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

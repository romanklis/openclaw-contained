"""
Database models
"""
from sqlalchemy import Column, String, Integer, DateTime, JSON, Enum as SQLEnum, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from datetime import datetime

Base = declarative_base()


class TaskStatus(str, enum.Enum):
    """Task execution status"""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskForceStatus(str, enum.Enum):
    """Task Force lifecycle status"""
    DRAFT = "draft"
    ACTIVE = "active"  # Registered as a virtual agent — reusable template
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CeremonyType(str, enum.Enum):
    """Types of coordination ceremonies"""
    PLANNING = "planning"
    SYNC = "sync"
    PEER_REVIEW = "peer_review"
    AGGREGATION = "aggregation"
    CUSTOM = "custom"


class CeremonyMode(str, enum.Enum):
    """Ceremony coordination mode"""
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"


class ExecutionEnvironment(str, enum.Enum):
    """Execution environment types"""
    DIND = "dind"
    DEDICATED_VM = "dedicated_vm"


class CapabilityType(str, enum.Enum):
    """Types of capabilities"""
    TOOL_INSTALL = "tool_install"
    NETWORK_ACCESS = "network_access"
    FILESYSTEM_ACCESS = "filesystem_access"
    DATABASE_ACCESS = "database_access"


class RequestStatus(str, enum.Enum):
    """Capability request status"""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    MODIFIED = "modified"


class DeploymentStatus(str, enum.Enum):
    """Deployment lifecycle status"""
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    BUILDING = "building"
    BUILT = "built"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class Task(Base):
    """Task model"""
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.CREATED)
    
    # Workspace and execution
    workspace_id = Column(String, nullable=False)
    current_image = Column(String)
    current_policy_id = Column(Integer, ForeignKey("policies.id"))
    llm_model = Column(String, default="gemma3:4b")
    agent_profile = Column(String)  # agent profile ID (e.g. 'performance-agent')
    
    # Temporal workflow
    workflow_id = Column(String, unique=True)
    workflow_run_id = Column(String)

    # Task Force membership (null for standalone tasks)
    task_force_id = Column(String, ForeignKey("task_forces.id"), nullable=True)
    task_force_role = Column(String)  # Role within the task force

    # Metadata
    created_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    
    # Relationships
    policy = relationship("Policy", foreign_keys=[current_policy_id])
    capability_requests = relationship("CapabilityRequest", back_populates="task")


class Policy(Base):
    """Policy model"""
    __tablename__ = "policies"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    version = Column(Integer, nullable=False)
    
    # Policy rules (JSON)
    tools_allowed = Column(JSON, default=list)
    network_rules = Column(JSON, default=dict)
    filesystem_rules = Column(JSON, default=dict)
    database_rules = Column(JSON, default=dict)
    resource_limits = Column(JSON, default=dict)
    
    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String)


class CapabilityRequest(Base):
    """Capability request model"""
    __tablename__ = "capability_requests"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    
    # Request details
    capability_type = Column(SQLEnum(CapabilityType), nullable=False)
    resource_name = Column(String, nullable=False)
    justification = Column(Text, nullable=False)
    details = Column(JSON)  # Additional structured data
    
    # Status and decision
    status = Column(SQLEnum(RequestStatus), default=RequestStatus.PENDING)
    decision_notes = Column(Text)
    alternative_suggestion = Column(Text)  # Suggested alternative approach
    reviewed_by = Column(String)  # Who reviewed (replaces decided_by)
    reviewed_at = Column(DateTime)  # When reviewed (replaces decided_at)
    decided_by = Column(String)  # Legacy field
    decided_at = Column(DateTime)  # Legacy field
    
    # Metadata
    requested_at = Column(DateTime, server_default=func.now())
    
    # Relationships
    task = relationship("Task", back_populates="capability_requests")


class TaskOutput(Base):
    """Stores output from each agent iteration"""
    __tablename__ = "task_outputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    iteration = Column(Integer, nullable=False)

    # Agent result fields
    completed = Column(String, default="false")  # "true"/"false"
    capability_requested = Column(String, default="false")
    agent_logs = Column(Text)  # Full wrapper stdout
    output = Column(Text)  # OpenClaw JSON output
    error = Column(Text)
    llm_response_preview = Column(Text)  # Preview from LLM router log
    model_used = Column(String)
    image_used = Column(String)
    duration_ms = Column(Integer)

    # Deliverable files created by the agent {filename: content}
    deliverables = Column(JSON)

    # Raw result JSON from the worker
    raw_result = Column(JSON)

    created_at = Column(DateTime, server_default=func.now())

    task = relationship("Task", backref="outputs")


class TaskMessage(Base):
    """Conversation messages between agent and user"""
    __tablename__ = "task_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)

    role = Column(String, nullable=False)  # "agent", "user", "system"
    content = Column(Text, nullable=False)
    msg_metadata = Column("metadata", JSON)  # Extra info (iteration, model, etc.)

    created_at = Column(DateTime, server_default=func.now())

    task = relationship("Task", backref="messages")


class LLMProviderConfig(Base):
    """Persistent LLM provider configuration (API keys, URLs)"""
    __tablename__ = "llm_provider_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)   # e.g. "GEMINI_API_KEY"
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Deployment(Base):
    """Deployment produced by a task"""
    __tablename__ = "deployments"

    id = Column(String, primary_key=True)  # deploy-<uuid8>
    name = Column(String, nullable=False)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)

    # Image
    image_tag = Column(String)  # registry tag of the deployment image
    entrypoint = Column(String)  # e.g. "python app.py"
    port = Column(Integer)  # primary exposed port

    # Runtime
    status = Column(SQLEnum(DeploymentStatus), default=DeploymentStatus.PENDING_APPROVAL)
    container_id = Column(String)  # docker container id when running
    host_port = Column(Integer)  # mapped host port when running
    url = Column(String)  # accessible URL when running

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    approved_at = Column(DateTime)
    built_at = Column(DateTime)
    started_at = Column(DateTime)
    stopped_at = Column(DateTime)
    error = Column(Text)

    task = relationship("Task", backref="deployments")


class AuditLog(Base):
    """Audit log for all actions"""
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"))
    user_id = Column(String)
    
    action = Column(String, nullable=False)
    resource_type = Column(String)
    resource_id = Column(String)
    details = Column(JSON)
    
    timestamp = Column(DateTime, server_default=func.now())


class SBOMFormat(str, enum.Enum):
    """SBOM output format"""
    SPDX_JSON = "spdx-json"
    CYCLONEDX_JSON = "cyclonedx-json"


class SBOM(Base):
    """Software Bill of Materials for agent container images.

    Each row stores a complete SBOM document (SPDX or CycloneDX) generated
    during the image-build step.  The `packages` column is a denormalised
    list of {name, version, type, license} dicts for fast searching.
    """
    __tablename__ = "sboms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    image_tag = Column(String, nullable=False)
    image_version = Column(Integer, nullable=False)  # matches v1, v2, …
    format = Column(SQLEnum(SBOMFormat), nullable=False)

    # Full SBOM document (SPDX / CycloneDX JSON)
    document = Column(JSON, nullable=False)

    # Denormalised package list for search
    # [{"name": "flask", "version": "3.0.0", "type": "pip", "license": "BSD-3"}]
    packages = Column(JSON, nullable=False, default=list)

    # Generation metadata
    generator = Column(String, default="trivy")  # trivy / syft
    generated_at = Column(DateTime, server_default=func.now())

    task = relationship("Task", backref="sboms")


# =========================================================================
# Task Force Models — Multi-Agent Orchestration
# =========================================================================

class TaskForce(Base):
    """A Task Force is a team of agents collaborating on a task.

    It defines the composition (which agents, their roles) and the
    coordination ceremonies (meeting points, sync events) that govern
    how agents interact.
    """
    __tablename__ = "task_forces"

    id = Column(String, primary_key=True)  # taskforce-<uuid8>
    name = Column(String, nullable=False)
    description = Column(Text)
    objective = Column(Text, nullable=False)  # The overall goal for the team

    # Execution environment
    execution_environment = Column(
        SQLEnum(ExecutionEnvironment),
        default=ExecutionEnvironment.DIND,
    )

    # Lifecycle
    status = Column(SQLEnum(TaskForceStatus), default=TaskForceStatus.DRAFT)
    workflow_id = Column(String, unique=True)
    workflow_run_id = Column(String)

    # Shared workspace for all agents in the force
    workspace_id = Column(String, nullable=False)

    # Metadata
    created_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    members = relationship("TaskForceMember", back_populates="task_force", cascade="all, delete-orphan")
    ceremonies = relationship("TaskForceCeremony", back_populates="task_force", cascade="all, delete-orphan")
    tasks = relationship("Task", backref="task_force", foreign_keys="Task.task_force_id")


class TaskForceMember(Base):
    """A member of a Task Force — an agent with a specific role.

    Each member maps to a real agent profile and is assigned a role
    that defines its responsibilities within the team.
    """
    __tablename__ = "task_force_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_force_id = Column(String, ForeignKey("task_forces.id"), nullable=False)

    # Agent identity
    agent_profile = Column(String, nullable=False)  # profile ID (e.g. "senior-reviewer")
    role = Column(String, nullable=False)  # e.g. "Researcher", "Developer", "Reviewer"
    responsibilities = Column(Text)  # Free-text description of what this agent should do

    # Execution config overrides
    llm_model = Column(String)  # Override profile's default LLM
    base_image = Column(String)  # Override profile's default image

    # Runtime state
    task_id = Column(String, ForeignKey("tasks.id"))  # The actual spawned task for this member
    status = Column(String, default="pending")  # pending, running, completed, failed

    # Order in which this member acts (for sequential ceremonies)
    execution_order = Column(Integer, default=0)

    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    task_force = relationship("TaskForce", back_populates="members")
    task = relationship("Task", foreign_keys=[task_id])


class TaskForceCeremony(Base):
    """A coordination ceremony within a Task Force.

    Ceremonies define synchronization points where agents meet, share
    context, review each other's work, or aggregate results.
    """
    __tablename__ = "task_force_ceremonies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_force_id = Column(String, ForeignKey("task_forces.id"), nullable=False)

    name = Column(String, nullable=False)  # e.g. "Planning Phase", "Code Review Sync"
    ceremony_type = Column(SQLEnum(CeremonyType), nullable=False)
    mode = Column(SQLEnum(CeremonyMode), default=CeremonyMode.SYNCHRONOUS)

    # Ordering: ceremonies execute in this order
    sequence_order = Column(Integer, nullable=False, default=0)

    # Which members participate (JSON list of member IDs; null = all)
    participant_member_ids = Column(JSON)

    # Configuration
    description = Column(Text)  # Instructions for this ceremony
    trigger_condition = Column(String)  # "after_all_complete", "after_member:<id>", "manual"
    timeout_minutes = Column(Integer, default=60)

    # Runtime state
    status = Column(String, default="pending")  # pending, active, completed, skipped
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    result_summary = Column(Text)  # Aggregated output from ceremony

    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    task_force = relationship("TaskForce", back_populates="ceremonies")

"""
Database models
"""
from sqlalchemy import Column, String, Integer, DateTime, JSON, Enum as SQLEnum, ForeignKey, Text, Boolean
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


class DAGStatus(str, enum.Enum):
    """Master DAG lifecycle status"""
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(str, enum.Enum):
    """DAG node execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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

    # DAG membership (null for standalone tasks)
    dag_id = Column(String, ForeignKey("master_dags.id"), nullable=True)
    node_id = Column(String)  # node_id within the DAG

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
    agent_image = Column(String)  # agent's committed image (used as deploy base)
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
# DAG Orchestration Models — Task-Centric Skill-Based Execution
# =========================================================================

class Skill(Base):
    """A reusable skill template defining a sequence of functional steps.

    Skills are the building blocks of DAGs. Each skill describes what
    inputs it needs, what artifacts it produces, and the ordered steps
    to achieve its goal. Steps are executed as individual DAG nodes.
    """
    __tablename__ = "skills"

    id = Column(String, primary_key=True)  # skill-<uuid8>
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    version = Column(Integer, default=1)

    # Full SKILL.md content — install instructions, usage, setup steps
    instructions = Column(Text, default="")

    # Schema definitions
    input_schema = Column(JSON, default=dict)   # required inputs {key: type_description}
    output_artifacts = Column(JSON, default=list)  # expected output file paths/patterns
    steps = Column(JSON, default=list)  # ordered list of sub-steps

    # ClawHub source metadata
    source_url = Column(String, default="")  # e.g. clawhub.ai slug

    # Searchability
    tags = Column(JSON, default=list)  # list of string tags

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class MasterDAG(Base):
    """A Master DAG representing a decomposed user objective.

    Created by the Planner from a user's objective and a set of skills.
    Contains the full DAG structure (nodes + edges) as JSON, and is
    executed by a Temporal DAGWorkflow.
    """
    __tablename__ = "master_dags"

    id = Column(String, primary_key=True)  # dag-<uuid8>
    objective = Column(Text, nullable=False)  # original user prompt
    status = Column(SQLEnum(DAGStatus), default=DAGStatus.PLANNING)

    # The full DAG structure
    dag_json = Column(JSON, nullable=False, default=dict)

    # Execution config
    workspace_id = Column(String, nullable=False)
    llm_model = Column(String, default="gemma3:4b")

    # Temporal workflow reference
    workflow_id = Column(String, unique=True)
    workflow_run_id = Column(String)

    # Metadata
    created_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    nodes = relationship("DAGNode", back_populates="dag", cascade="all, delete-orphan")
    tasks = relationship("Task", backref="dag", foreign_keys="Task.dag_id")


class DAGNode(Base):
    """A single node in a Master DAG.

    Each node represents one unit of work — typically one step from a
    skill, or an inline custom task. Nodes track their dependencies,
    execution status, and output data.
    """
    __tablename__ = "dag_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dag_id = Column(String, ForeignKey("master_dags.id"), nullable=False, index=True)
    node_id = Column(String, nullable=False)  # unique within DAG (e.g. "search-1")

    # Skill reference (nullable for inline/custom nodes)
    skill_id = Column(String, ForeignKey("skills.id"), nullable=True)
    skill_step_index = Column(Integer)  # which step within the skill

    # Execution
    description = Column(Text)
    status = Column(SQLEnum(NodeStatus), default=NodeStatus.PENDING)
    depends_on = Column(JSON, default=list)  # list of node_ids
    config = Column(JSON, default=dict)  # overrides: base_image, llm_model, env_id, timeout, deploy_authorized
    input_mapping = Column(JSON, default=dict)  # maps inputs to dependency outputs
    output_data = Column(JSON)  # captured results after execution

    # Runtime
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    container_id = Column(String)

    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    dag = relationship("MasterDAG", back_populates="nodes")
    skill = relationship("Skill")
    task = relationship("Task", foreign_keys=[task_id])


class NodeEnvironment(Base):
    """A reusable execution environment with approved capabilities.

    Environments decouple the execution context from individual tasks.
    They accumulate approved packages over time and can be referenced
    by any DAG node. They can also be forked to create variants.
    """
    __tablename__ = "node_environments"

    id = Column(String, primary_key=True)  # env-<uuid8>
    name = Column(String, nullable=False)
    description = Column(Text)

    # Capability tracking
    capability_fingerprint = Column(String, index=True)  # sorted SHA256 of capabilities
    capabilities = Column(JSON, default=list)  # list of approved packages/tools
    base_image = Column(String, default="openclaw")  # openclaw/nanobot/picoclaw/zeroclaw
    current_image_tag = Column(String)  # registry tag (e.g. localhost:5000/openclaw-agent:env-abc123-v3)
    version = Column(Integer, default=0)  # increments on each capability addition

    # Forking
    parent_env_id = Column(String, ForeignKey("node_environments.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    parent = relationship("NodeEnvironment", remote_side="NodeEnvironment.id")


class SupplyChainPackage(Base):
    """A single approved package in the supply-chain allowlist.

    Each row represents one package that an agent is allowed to install
    for a given image type and package manager.
    """
    __tablename__ = "supply_chain_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_type = Column(String, nullable=False, index=True)  # e.g. openclaw, nanobot
    manager = Column(String, nullable=False)  # pip, apt, apk, npm
    package_name = Column(String, nullable=False)
    notes = Column(Text)
    is_exception = Column(String, default="false")  # "true" for one-off exceptions

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class SupplyChainAlias(Base):
    """Cross-distro package name mapping (apt ↔ apk)."""
    __tablename__ = "supply_chain_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction = Column(String, nullable=False)  # "apt_to_apk" or "apk_to_apt"
    from_name = Column(String, nullable=False)
    to_name = Column(String, nullable=False)

    created_at = Column(DateTime, server_default=func.now())


class SupplyChainImageType(Base):
    """Metadata for each image type in the supply chain."""
    __tablename__ = "supply_chain_image_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_type = Column(String, unique=True, nullable=False)
    notes = Column(Text)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class AgentImage(Base):
    """A named base image that the planner can assign to DAG nodes.

    Rows are seeded from agent_profiles.yaml at startup (if table is empty)
    and can be added/updated via the /api/agent-images CRUD API.
    Users nominate post-build images here so the planner can discover them.
    """
    __tablename__ = "agent_images"

    # Logical name — used as base_image value in DAG node configs (e.g. "browser")
    id = Column(String, primary_key=True)
    # Human-readable label (e.g. "Web Agent")
    name = Column(String, nullable=False)
    # Full description shown to the planner LLM for image selection
    description = Column(Text, default="")
    # Registry tag (e.g. "openclaw-agent:browser") — informational
    tag = Column(String, default="")
    # Whether this image is currently selectable by the planner
    enabled = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
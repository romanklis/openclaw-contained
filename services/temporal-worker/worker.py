"""
Temporal Worker - Executes workflows and activities
"""
import asyncio
import logging
import re
import uuid
from temporalio import workflow, activity
from temporalio.exceptions import ApplicationError
from temporalio.client import Client
from temporalio.worker import Worker
from datetime import timedelta, datetime, timezone
from typing import Dict, Any, List, Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Known image types are loaded lazily from the control-plane API the first time
# supply-chain detection needs them. Falls back to a built-in default so the
# worker is functional before the control-plane is reachable.
_KNOWN_IMAGE_TYPES_CACHE: tuple | None = None
_KNOWN_IMAGE_TYPES_DEFAULT = ("zeroclaw", "nanobot", "picoclaw", "browser", "openclaw", "octaveclaw")


async def _fetch_known_image_types() -> tuple:
    """Fetch enabled agent image IDs from the control-plane API."""
    global _KNOWN_IMAGE_TYPES_CACHE
    if _KNOWN_IMAGE_TYPES_CACHE is not None:
        return _KNOWN_IMAGE_TYPES_CACHE
    try:
        import httpx as _httpx
        control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{control_plane_url}/api/agent-images?enabled_only=true")
            if resp.status_code == 200:
                names = tuple(img["id"] for img in resp.json())
                if names:
                    _KNOWN_IMAGE_TYPES_CACHE = names
                    return names
    except Exception as exc:
        logger.debug("Could not fetch agent image types from API: %s", exc)
    return _KNOWN_IMAGE_TYPES_DEFAULT

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "openclaw-tasks")


def is_gemini_lite_model(model: str) -> bool:
    normalized = (model or "").strip().lower()
    return "gemini" in normalized and "lite" in normalized


def _parse_capability_request_marker(text: str) -> Optional[Dict[str, str]]:
    """Parse CAPABILITY_REQUEST marker text into a capability payload.

    Expected format:
    CAPABILITY_REQUEST:<type>:<resource>:<justification>
    """
    if not isinstance(text, str):
        return None
    marker_line = ""
    for line in text.splitlines():
        if "CAPABILITY_REQUEST:" in line:
            marker_line = line.strip()
            break
    if not marker_line:
        return None
    payload = marker_line.split("CAPABILITY_REQUEST:", 1)[1].strip()
    parts = payload.split(":", 2)
    if len(parts) < 2:
        return None
    cap_type = (parts[0] or "tool_install").strip() or "tool_install"
    resource = (parts[1] or "").strip()
    justification = (parts[2] if len(parts) > 2 else "Requested by agent").strip()
    return {
        "type": cap_type,
        "resource": resource,
        "justification": justification,
    }


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    """Parse common ISO datetime string formats into datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            # Normalize to naive UTC so comparisons with datetime.now(timezone.utc)
            # are deterministic inside workflow sandbox code.
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None

# =============================================================================
# Agent Sandbox Configuration
# =============================================================================
AGENT_SANDBOX_MODE = os.getenv("AGENT_SANDBOX_MODE", "insecure-dind")

if AGENT_SANDBOX_MODE == "insecure-dind":
    logger.warning(
        "⚠️  CRITICAL SECURITY RISK: Agent sandbox is running in 'insecure-dind' mode. "
        "Agents execute in privileged containers with potential host-root access. "
        "Set AGENT_SANDBOX_MODE=gvisor in your .env for production deployments."
    )
elif AGENT_SANDBOX_MODE == "gvisor":
    logger.info("✅ Agent sandbox configured securely with gVisor (runsc).")
else:
    logger.error(f"❌ Unknown AGENT_SANDBOX_MODE='{AGENT_SANDBOX_MODE}'. Valid values: gvisor, insecure-dind")


# Cached Docker client — created once, reused for all activities.
# By passing an explicit ``version`` we skip the ``/version`` round-trip
# that ``docker.from_env()`` does on every call.  DinD 24.0.9 → API 1.43.
_docker_client = None
_docker_client_lock = __import__("threading").Lock()

DIND_API_VERSION = "1.43"


def get_docker_client():
    """Return a cached Docker client connected to the DinD daemon.

    Both ``gvisor`` and ``insecure-dind`` modes use the same DinD sidecar
    (``DOCKER_HOST=tcp://docker-dind:2375``).  In gVisor mode the DinD
    daemon has ``runsc`` installed and registered as a runtime, so
    ``runtime='runsc'`` is passed at container-creation time.

    The client is created lazily on the first call with an explicit API
    version so no ``/version`` request is made.  If the connection was
    broken (e.g. DinD restarted) we detect the stale client and rebuild it.
    """
    import docker

    global _docker_client

    # Fast path — reuse existing client and verify it's alive with a
    # lightweight ping (/_ping returns "OK" and is cheaper than /version).
    if _docker_client is not None:
        try:
            _docker_client.ping()
            return _docker_client
        except Exception:
            logger.warning("⚠️ Cached Docker client stale — reconnecting")
            _docker_client = None

    with _docker_client_lock:
        # Double-check after acquiring lock
        if _docker_client is not None:
            return _docker_client

        import time as _time
        retries, backoff = 5, 3.0
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                _docker_client = docker.DockerClient(
                    base_url=os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"),
                    timeout=300,
                    version=DIND_API_VERSION,
                )
                _docker_client.ping()
                logger.info("✅ Docker client connected (API %s)", DIND_API_VERSION)
                return _docker_client
            except Exception as exc:
                last_err = exc
                _docker_client = None
                if attempt < retries:
                    wait = backoff * attempt
                    logger.warning(
                        f"⚠️ DinD not ready (attempt {attempt}/{retries}): {exc} "
                        f"— retrying in {wait:.0f}s"
                    )
                    _time.sleep(wait)
        raise last_err  # type: ignore[misc]


# =============================================================================
# Workflows
# =============================================================================

@workflow.defn
class AgentTaskWorkflow:
    """Main workflow for agent task execution"""
    
    def __init__(self):
        self.approval_received = False
        self.capability_approved = False
        self.current_image = "localhost:5000/openclaw-agent:openclaw"  # Track current agent image (default)
        self.llm_model = "gemma3:4b"  # Track LLM model
        self.follow_up = ""  # Follow-up instructions for continuation
        self._capability_feedback = ""  # one-shot feedback after a build (cleared after use)
        self._trial_failures = 0  # Track trial deployment failures
        self._trial_rework_pending = False  # True when agent must fix a trial failure
        self._approved_capabilities = set()  # Track already-approved resource names to prevent loops

    async def _resolve_capability_state(
        self,
        task_id: str,
        iteration_started_at: datetime,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolve capability lifecycle state for current iteration.

        Returns one of:
        - none: no capability evidence for this iteration
        - requested_pending: capability request exists and should block completion
        - requested_resolved: capability request was already approved/modified
        """
        capability = result.get("capability") if isinstance(result.get("capability"), dict) else {}
        evidence: List[str] = []

        if result.get("capability_requested"):
            evidence.append("agent_result")

        deliverables = result.get("deliverables") if isinstance(result.get("deliverables"), dict) else {}
        request_txt = deliverables.get("request.txt")
        marker_capability = None
        if isinstance(request_txt, str) and "CAPABILITY_REQUEST:" in request_txt:
            evidence.append("request_artifact")
            marker_capability = _parse_capability_request_marker(request_txt)
            if marker_capability and not capability:
                capability = marker_capability

        capability_rows = await workflow.execute_activity(
            list_task_capability_requests,
            args=[task_id],
            start_to_close_timeout=timedelta(seconds=20),
        )

        pending_row = None
        resolved_row = None
        ts_ref = iteration_started_at
        if ts_ref.tzinfo is not None:
            ts_ref = ts_ref.astimezone(timezone.utc).replace(tzinfo=None)
        window_start = ts_ref - timedelta(minutes=5)
        for row in capability_rows or []:
            requested_at = _parse_iso_datetime(row.get("requested_at"))
            if requested_at and requested_at < window_start:
                continue
            status = (row.get("status") or "").lower()
            if status == "pending" and pending_row is None:
                pending_row = row
            elif status in {"approved", "modified"} and resolved_row is None:
                resolved_row = row
            if pending_row and resolved_row:
                break

        if pending_row:
            if not capability:
                capability = {
                    "type": pending_row.get("capability_type", "tool_install"),
                    "resource": pending_row.get("resource_name", ""),
                    "justification": pending_row.get("justification", ""),
                }
            return {
                "state": "requested_pending",
                "capability": capability,
                "source": "control_plane_pending",
                "should_create_request": False,
            }

        if resolved_row:
            if not capability:
                capability = {
                    "type": resolved_row.get("capability_type", "tool_install"),
                    "resource": resolved_row.get("resource_name", ""),
                    "justification": resolved_row.get("justification", ""),
                }
            return {
                "state": "requested_resolved",
                "capability": capability,
                "source": f"control_plane_{(resolved_row.get('status') or '').lower()}",
                "should_create_request": False,
            }

        if result.get("capability_requested") or marker_capability:
            return {
                "state": "requested_pending",
                "capability": capability or marker_capability or {},
                "source": "agent_signal",
                "should_create_request": True,
            }

        return {
            "state": "none",
            "capability": {},
            "source": "none",
            "should_create_request": False,
        }
    
    @workflow.run
    async def run(
        self,
        task_id: str,
        llm_model: str = "gemma3:4b",
        current_image: str = "",
        follow_up: str = "",
        dag_id: str = "",
    ) -> Dict[str, Any]:
        """Execute agent task.

        For first-run workflows ``current_image`` can be a base image tag
        (e.g. ``localhost:5000/openclaw-agent:zeroclaw``) or empty for the
        default openclaw image.
        For continuation workflows it carries over from the previous run:
        - ``current_image``: the last built agent image (all packages installed)
        - ``follow_up``: user's follow-up instructions

        When ``dag_id`` is set the workflow runs inside a DAG; after the
        last iteration the agent container is committed to a new image so
        downstream nodes inherit the file-system state.
        """
        
        self.llm_model = llm_model
        self.follow_up = follow_up
        self.dag_id = dag_id

        # Use the provided base image for both first-run and continuation
        if current_image:
            self.current_image = current_image
            if follow_up:
                logger.info(f"♻️  CONTINUATION workflow for task {task_id} | image={current_image} | follow_up={follow_up[:120]}...")
            else:
                logger.info(f"Starting workflow for task {task_id} with model {llm_model}, base image {current_image}")
        else:
            logger.info(f"Starting workflow for task {task_id} with model {llm_model} (default openclaw image)")
        
        # Step 1: Initialize task
        await workflow.execute_activity(
            initialize_task,
            args=[task_id],
            start_to_close_timeout=timedelta(seconds=30)
        )
        
        # Determine starting iteration.
        # For continuations, fetch the last iteration number so we don't overwrite.
        start_iteration = 0
        if follow_up:  # this is a continuation (has follow-up instructions)
            start_iteration = await workflow.execute_activity(
                get_last_iteration,
                args=[task_id],
                start_to_close_timeout=timedelta(seconds=15)
            )
            logger.info(f"♻️  Continuing from iteration {start_iteration}")

        # Step 2: Agent execution loop
        max_iterations = 50
        iteration = start_iteration
        
        while iteration < max_iterations:
            iteration += 1
            iteration_started_at = workflow.now()
            
            # Heartbeat: update task status to RUNNING at start of each iteration
            await workflow.execute_activity(
                update_task_status,
                args=[task_id, "running"],
                start_to_close_timeout=timedelta(seconds=15),
            )

            logger.info(f"Task {task_id} iteration {iteration} with image {self.current_image}")
            
            # Execute agent step as a child workflow so every LLM turn
            # inside it is visible as a separate activity in Temporal UI.
            # If there's pending capability feedback, inject it into this
            # iteration's follow_up and clear it (one-shot).
            iter_follow_up = self.follow_up
            if self._capability_feedback:
                iter_follow_up = (
                    (iter_follow_up + "\n\n" if iter_follow_up else "")
                    + self._capability_feedback
                )
                self._capability_feedback = ""  # one-shot — don't repeat

            result = await workflow.execute_child_workflow(
                AgentStepWorkflow.run,
                args=[task_id, iteration, self.current_image, self.llm_model, iter_follow_up, self.dag_id],
                id=f"agent-step-{task_id}-iter-{iteration}",
            )

            # If a DAG container was committed, update current_image so
            # subsequent iterations (and eventually the parent DAGNodeWorkflow)
            # use the enriched image.
            _committed = result.get("committed_image")
            if _committed:
                self.current_image = _committed
                logger.info(f"📸 DAG image updated from commit: {_committed}")

            # Store output in the control-plane database (fire-and-forget, don't block workflow)
            try:
                await workflow.execute_activity(
                    store_task_output,
                    args=[task_id, iteration, result, self.current_image, self.llm_model],
                    start_to_close_timeout=timedelta(seconds=30)
                )
            except Exception:
                pass  # Non-critical — don't fail the workflow if output storage fails

            # Check if agent hard-failed (container crash, exit non-zero, etc.)
            if result.get("agent_failed"):
                logger.error(f"Task {task_id} agent failed at iteration {iteration}: {result.get('error', 'unknown')[:200]}")
                # Update task status to failed
                await workflow.execute_activity(
                    finalize_task,
                    args=[task_id, "failed"],
                    start_to_close_timeout=timedelta(minutes=5)
                )
                return {
                    "status": "failed",
                    "error": result.get("error", "Agent execution failed"),
                    "iteration": iteration,
                }
            
            # Check if deployment requested
            if result.get("deployment_requested"):
                deployment = result.get("deployment", {})
                logger.info(f"🚀 DEPLOYMENT_REQUEST | Task: {task_id} | Name: {deployment.get('name')} | Port: {deployment.get('port')}")

                # Check if this task/node is authorized to deploy
                should_deploy = True
                deploy_info = await workflow.execute_activity(
                    check_deploy_authority,
                    args=[task_id],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                if not deploy_info.get("can_deploy"):
                    logger.info(
                        f"🚫 DEPLOY_SUPPRESSED | {task_id} — {deploy_info.get('reason')}"
                    )
                    should_deploy = False
                    # Node finished its work but isn't authorized to deploy.
                    # Treat as completed — the deploy-app node will handle deployment.
                    break

                if should_deploy:
                    # Inject the agent's current image so the deployment builder
                    # can use it as the base — guarantees all approved deps are present.
                    deployment["agent_image"] = self.current_image

                    # Ensure common servers bind to 0.0.0.0 (not localhost)
                    deployment["entrypoint"] = _normalize_deployment_entrypoint(
                        deployment.get("entrypoint", "python app.py"),
                        deployment.get("port", 5000),
                    )

                    # --- Trial deployment: build + health-check before real deploy ---
                    trial_result = await workflow.execute_activity(
                        trial_deploy,
                        args=[task_id, deployment],
                        start_to_close_timeout=timedelta(minutes=10),
                    )

                    if not trial_result.get("passed"):
                        self._trial_failures += 1
                        trial_error = trial_result.get("error", "Unknown trial error")
                        trial_phase = trial_result.get("phase") or "unknown"
                        trial_logs = trial_result.get("logs", "")
                        logger.warning(
                            f"🧪 TRIAL_FAILED ({self._trial_failures}/3) | {task_id} | Phase: {trial_phase} | {trial_error[:200]}"
                        )

                        if self._trial_failures >= 3:
                            # Max retries — proceed to real deployment anyway
                            logger.error(f"🧪 Max trial failures reached for {task_id} — proceeding to real deployment")
                            deploy_result = await workflow.execute_activity(
                                create_deployment,
                                args=[task_id, deployment],
                                start_to_close_timeout=timedelta(seconds=30),
                            )
                            logger.info(f"📦 Deployment created (after trial failures): {deploy_result.get('id')}")
                            self._trial_rework_pending = False
                            break

                        # Build concise feedback for the agent
                        feedback_lines = [
                            "TRIAL DEPLOYMENT FAILED.",
                            f"Phase: {trial_phase}",
                            f"Error: {trial_error[:500]}",
                        ]
                        if trial_logs:
                            feedback_lines.append(f"Logs: {trial_logs[:800]}")
                        feedback_lines.append("Fix the code and emit DEPLOYMENT_REQUEST again.")
                        self._capability_feedback = "\n".join(feedback_lines)
                        self._trial_rework_pending = True
                        continue  # Don't break — let agent fix and retry

                    # Trial passed — create deployment with pre-built image tag
                    logger.info(f"🧪 ✅ Trial passed — proceeding to real deployment")
                    self._trial_rework_pending = False
                    deployment["_trial_image_tag"] = trial_result.get("image_tag")
                    deploy_result = await workflow.execute_activity(
                        create_deployment,
                        args=[task_id, deployment],
                        start_to_close_timeout=timedelta(seconds=30)
                    )
                    logger.info(f"📦 Deployment created: {deploy_result.get('id')}")
                break
            
            # Resolve capability lifecycle before completion. This prevents
            # completed=true from skipping capability handling in fast approval paths.
            capability_state = await self._resolve_capability_state(
                task_id,
                iteration_started_at,
                result,
            )
            cap_state = capability_state.get("state", "none")
            capability = capability_state.get("capability") or {}
            resource_name = capability.get("resource", "") if isinstance(capability, dict) else ""

            if cap_state == "requested_resolved":
                logger.info(
                    f"🔐 Capability already resolved for {task_id} "
                    f"(source={capability_state.get('source')}, resource={resource_name or 'unknown'})"
                )
                if resource_name:
                    self._approved_capabilities.add(resource_name)
                # Capability activity happened this iteration; continue so completion
                # is evaluated on the next clean iteration.
                continue

            if cap_state == "requested_pending":
                logger.info(
                    f"🔐 Capability precedence for {task_id}: source={capability_state.get('source')} "
                    f"resource={resource_name or 'unknown'}"
                )

                # Guard: skip if the same resource was already approved
                # (prevents infinite loops from stale results or LLM re-requesting)
                already_approved = resource_name in self._approved_capabilities
                if already_approved:
                    logger.info(
                        f"⏭️ Skipping duplicate capability request for '{resource_name}' "
                        f"— already approved this run"
                    )
                    await workflow.execute_activity(
                        dismiss_pending_capabilities,
                        args=[task_id],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    continue

                if capability_state.get("should_create_request"):
                    await workflow.execute_activity(
                        create_capability_request,
                        args=[task_id, capability],
                        start_to_close_timeout=timedelta(seconds=30),
                    )

                # Reset signal flags before waiting (prevents stale signals
                # from a prior iteration from immediately satisfying the wait)
                self.approval_received = False
                self.capability_approved = False

                # Update status: waiting for human approval
                await workflow.execute_activity(
                    update_task_status,
                    args=[task_id, "waiting_approval"],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                # Wait for approval signal (workflow pauses here)
                await workflow.wait_condition(
                    lambda: self.approval_received,
                    timeout=timedelta(hours=24)
                )

                if self.capability_approved:
                    # Update status: building new agent image
                    await workflow.execute_activity(
                        update_task_status,
                        args=[task_id, "building_image"],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    await workflow.execute_activity(
                        add_to_supply_chain,
                        args=[task_id, capability, []],
                        start_to_close_timeout=timedelta(seconds=30),
                    )

                    await workflow.execute_activity(
                        reload_supply_chain,
                        args=[],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    build_result = await workflow.execute_activity(
                        build_agent_image,
                        args=[task_id, capability, self.current_image],
                        start_to_close_timeout=timedelta(minutes=10)
                    )

                    new_image = build_result.get("image", self.current_image)
                    supply_chain_feedback = build_result.get("feedback", "")
                    self.current_image = new_image
                    logger.info(f"Updated task image to {new_image}")

                    # Update status: back to running after build
                    await workflow.execute_activity(
                        update_task_status,
                        args=[task_id, "running"],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    if resource_name:
                        self._approved_capabilities.add(resource_name)

                    if supply_chain_feedback:
                        logger.warning(f"🚫 Supply-chain feedback for agent: {supply_chain_feedback[:200]}")
                        self._capability_feedback = (
                            "--- SYSTEM NOTICE ---\n"
                            + supply_chain_feedback
                            + "\n--- END NOTICE ---"
                        )

                    await workflow.execute_activity(
                        update_task_policy,
                        args=[task_id, capability, new_image],
                        start_to_close_timeout=timedelta(seconds=30)
                    )

                    await workflow.execute_activity(
                        dismiss_pending_capabilities,
                        args=[task_id],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    logger.info(f"Task {task_id} resumed with new capability")
                else:
                    logger.info(f"Capability request denied for task {task_id}")
                    self._capability_feedback = (
                        "--- SYSTEM NOTICE ---\n"
                        + f"CAPABILITY_DENIED: Your request for '{capability.get('resource', '')}' "
                        + "was denied by the operator. Find an alternative approach.\n"
                        + "--- END NOTICE ---"
                    )

                    # Update status: back to running after denial
                    await workflow.execute_activity(
                        update_task_status,
                        args=[task_id, "running"],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                self.approval_received = False
                self.capability_approved = False

                try:
                    guard = await workflow.execute_activity(
                        check_verdict_guard,
                        args=[task_id],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    if guard.get("verdict") == "pass":
                        logger.info(
                            f"✅ Verdict guard: PASS already submitted for {task_id} "
                            f"— stopping iterations after capability rebuild"
                        )
                        break
                except Exception:
                    pass  # guard is best-effort; don't block on failure

                # Capability branch always defers completion to a subsequent
                # iteration after lifecycle reconciliation.
                continue

            # Check if task complete
            if result.get("completed"):
                if self._trial_rework_pending:
                    # Agent completed without re-emitting DEPLOYMENT_REQUEST after trial failure.
                    # Force it to continue — re-inject the trial failure feedback.
                    logger.warning(
                        f"🧪 Agent completed without re-deploying after trial failure | {task_id} | Forcing rework"
                    )
                    self._capability_feedback = (
                        "TRIAL DEPLOYMENT FAILED previously. Your app does not start correctly.\n"
                        "You must fix the issue and emit DEPLOYMENT_REQUEST again."
                    )
                    continue
                break

        # Step 3: Finalize task
        final_result = await workflow.execute_activity(
            finalize_task,
            args=[task_id],
            start_to_close_timeout=timedelta(minutes=5)
        )
        
        # Expose the (possibly capability-enriched) image so parent
        # DAGNodeWorkflow / DAGWorkflow can propagate it to later nodes.
        final_result["current_image"] = self.current_image
        return final_result
    
    @workflow.signal
    async def approve_capability(self, approved: bool):
        """Signal to approve/deny capability"""
        self.approval_received = True
        self.capability_approved = approved


# =============================================================================
# AgentStepWorkflow — child workflow that breaks a single agent iteration
# into individually visible activities in Temporal UI.
#
# Instead of one monolithic "run_agent_step" activity, the workflow:
#   1. start_agent_container  — launches the container (detached)
#   2. poll_agent_turns       — polls the LLM router for new turns while
#                               the container runs, recording each as a
#                               record_agent_turn activity
#   3. collect_agent_result   — reads the final result after container exits
# =============================================================================

@workflow.defn
class AgentStepWorkflow:
    """Child workflow that provides per-turn visibility into an agent step."""

    @workflow.run
    async def run(
        self,
        task_id: str,
        iteration: int,
        agent_image: str = "localhost:5000/openclaw-agent:openclaw",
        llm_model: str = "gemma3:4b",
        follow_up: str = "",
        dag_id: str = "",
    ) -> Dict[str, Any]:
        logger.info(
            f"🔬 AgentStepWorkflow | Task: {task_id} | Iteration: {iteration} | "
            f"Image: {agent_image} | Model: {llm_model}"
        )

        # 1. Launch the container (returns container_id + workspace info)
        launch_info = await workflow.execute_activity(
            start_agent_container,
            args=[task_id, iteration, agent_image, llm_model, follow_up],
            start_to_close_timeout=timedelta(minutes=5),
        )

        if launch_info.get("error"):
            return {
                "completed": False,
                "agent_failed": True,
                "error": launch_info["error"],
            }

        container_id = launch_info["container_id"]
        workspace_dir = launch_info["workspace_dir"]
        turns_seen = 0

        # 2. Poll loop — keep checking for new LLM turns until container exits
        container_done = False
        while not container_done:
            poll_result = await workflow.execute_activity(
                poll_agent_turns,
                args=[task_id, container_id, turns_seen],
                start_to_close_timeout=timedelta(minutes=31),
                heartbeat_timeout=timedelta(seconds=60),
            )

            container_done = poll_result["container_done"]
            new_turns = poll_result.get("new_turns", [])

            # Record each new turn as its own activity
            for turn_data in new_turns:
                turns_seen += 1
                try:
                    await workflow.execute_activity(
                        record_agent_turn,
                        args=[task_id, iteration, turns_seen, turn_data],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                except Exception:
                    pass  # non-critical

        # 3. Collect the final result from the container
        result = await workflow.execute_activity(
            collect_agent_result,
            args=[task_id, iteration, container_id, workspace_dir, agent_image, llm_model, dag_id],
            start_to_close_timeout=timedelta(minutes=2),
        )

        # Record any remaining turns that arrived between last poll and container exit.
        # _remaining_turns contains ALL interactions; skip the ones already recorded.
        all_turns = result.pop("_remaining_turns", [])
        remaining_turns = all_turns[turns_seen:]
        for turn_data in remaining_turns:
            turns_seen += 1
            try:
                await workflow.execute_activity(
                    record_agent_turn,
                    args=[task_id, iteration, turns_seen, turn_data],
                    start_to_close_timeout=timedelta(seconds=15),
                )
            except Exception:
                pass

        logger.info(
            f"🔬 AgentStepWorkflow done | Task: {task_id} | Iteration: {iteration} | "
            f"Turns: {turns_seen} | Completed: {result.get('completed')}"
        )
        return result


# =============================================================================
# Activities
# =============================================================================

@activity.defn
async def initialize_task(task_id: str) -> Dict[str, Any]:
    """Initialize task execution environment"""
    logger.info(f"🚀 INITIALIZE | Task: {task_id} | Setting up execution environment")
    
    # TODO: Create workspace directory
    # TODO: Load initial policy
    # TODO: Pull base agent image
    
    return {"status": "initialized"}


@activity.defn
async def start_agent_container(
    task_id: str,
    iteration: int,
    agent_image: str = "localhost:5000/openclaw-agent:openclaw",
    llm_model: str = "gemma3:4b",
    follow_up: str = "",
) -> Dict[str, Any]:
    """Launch the agent container (detached) and return container_id + workspace_dir.

    This replaces the first half of the old monolithic ``run_agent_step``:
    image resolution, workspace setup, environment, and ``docker run``.
    The container is started in detached mode so control returns immediately.
    """
    logger.info(f"🚀 START_CONTAINER | Task: {task_id} | Iter: {iteration} | Image: {agent_image} | Model: {llm_model}")

    import docker

    try:
        docker_client = get_docker_client()

        # --- resolve image ---
        # Determine the canonical registry tag for pulling.
        agent_image_pull = agent_image.replace("localhost:5000/", "registry:5000/")
        if not agent_image_pull.startswith("registry:5000/"):
            agent_image_pull = f"registry:5000/{agent_image_pull}"

        # Always pull base images (tags without -v suffix) so we pick up
        # rebuilds immediately.  Versioned images (task-xxx-v2) are immutable
        # and safe to use from cache.
        tag_part = agent_image_pull.rsplit(":", 1)[-1] if ":" in agent_image_pull else ""
        is_versioned = bool(re.search(r'-v\d+$', tag_part))

        if not is_versioned:
            # Base / mutable tag — always pull latest from registry
            try:
                logger.info(f"📥 Pulling latest {agent_image_pull} (mutable tag)")
                docker_client.images.pull(agent_image_pull)
                agent_image = agent_image_pull
            except Exception as pull_err:
                logger.warning(f"⚠️  Pull failed ({pull_err}), falling back to local cache")
                # Fall back to whatever is locally available
                for variant in [agent_image_pull, agent_image,
                                agent_image.replace("localhost:5000/", ""),
                                agent_image.replace("registry:5000/", "")]:
                    try:
                        docker_client.images.get(variant)
                        agent_image = variant
                        break
                    except docker.errors.ImageNotFound:
                        continue
        else:
            # Versioned / immutable tag — use cache, pull only if missing
            image_found = False
            image_variants = [
                agent_image,
                agent_image_pull,
                agent_image.replace("localhost:5000/", ""),
                agent_image.replace("registry:5000/", ""),
            ]
            seen = set()
            image_variants = [v for v in image_variants if v not in seen and not seen.add(v)]

            for variant in image_variants:
                try:
                    docker_client.images.get(variant)
                    logger.info(f"✅ Image found locally as: {variant}")
                    agent_image = variant
                    image_found = True
                    break
                except docker.errors.ImageNotFound:
                    continue

            if not image_found:
                logger.info(f"📥 Pulling {agent_image_pull}")
                docker_client.images.pull(agent_image_pull)
                agent_image = agent_image_pull

        # --- workspace ---
        workspaces_root = "/workspaces"
        workspace_id = ""
        task_description = ""
        dag_id = ""
        node_id = ""
        try:
            import httpx as _httpx
            _cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            async with _httpx.AsyncClient(timeout=10.0) as _client:
                _resp = await _client.get(f"{_cp_url}/api/tasks/{task_id}")
                if _resp.status_code == 200:
                    _task_data = _resp.json()
                    workspace_id = _task_data.get("workspace_id", "")
                    task_description = _task_data.get("description", "")
                    dag_id = _task_data.get("dag_id", "") or ""
                    node_id = _task_data.get("node_id", "") or ""
        except Exception as _e:
            logger.warning(f"⚠️ Could not fetch task details: {_e}")

        if not workspace_id:
            workspace_id = f"workspace-{task_id}"

        # --- skill instructions discovery ---
        # If this task is a DAG node with a selected v2 skill, fetch that
        # skill's instructions first. Fall back to legacy skill_id.
        skill_instructions = ""
        if dag_id and node_id:
            try:
                import httpx as _httpx_skill
                _cp_skill = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
                async with _httpx_skill.AsyncClient(timeout=10.0) as _hc_skill:
                    _dag_resp = await _hc_skill.get(f"{_cp_skill}/api/dags/{dag_id}")
                    if _dag_resp.status_code == 200:
                        _dag_data = _dag_resp.json()
                        for _n in _dag_data.get("nodes", []):
                            if _n.get("node_id") != node_id:
                                continue

                            _selected_v2 = _n.get("selected_skill_v2_id")
                            _legacy_skill = _n.get("skill_id")
                            _skill_override = (_n.get("config") or {}).get("template_skill_instructions")

                            if _skill_override:
                                skill_instructions = str(_skill_override)
                                logger.info(
                                    f"📚 Parameterized (template) skill instructions used for {node_id} "
                                    f"({len(skill_instructions)} chars)"
                                )
                            elif _selected_v2:
                                _skill_resp = await _hc_skill.get(
                                    f"{_cp_skill}/api/skill-learning/skills/{_selected_v2}"
                                )
                                if _skill_resp.status_code == 200:
                                    skill_instructions = _skill_resp.json().get("instructions", "") or ""
                                    if skill_instructions:
                                        logger.info(
                                            f"📚 V2 skill instructions loaded for {node_id} "
                                            f"(skill: {_selected_v2}, {len(skill_instructions)} chars)"
                                        )

                            if not skill_instructions and _legacy_skill:
                                _skill_resp = await _hc_skill.get(
                                    f"{_cp_skill}/api/skills/{_legacy_skill}"
                                )
                                if _skill_resp.status_code == 200:
                                    skill_instructions = _skill_resp.json().get("instructions", "") or ""
                                    if skill_instructions:
                                        logger.info(
                                            f"📚 Legacy skill instructions loaded for {node_id} "
                                            f"(skill: {_legacy_skill}, {len(skill_instructions)} chars)"
                                        )
                                break
            except Exception as _skill_err:
                logger.warning(f"⚠️ Could not fetch skill instructions: {_skill_err}")

        # --- pre-installed packages discovery ---
        # Query approved capability requests so the agent knows what's
        # already baked into its image and doesn't re-request them.
        pre_installed_packages = ""
        try:
            import httpx as _httpx2
            _cp2 = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            async with _httpx2.AsyncClient(timeout=10.0) as _hc2:
                # Query by dag_id if available, otherwise by task_id
                _q = f"dag_id={dag_id}" if dag_id else f"task_id={task_id}"
                _cr = await _hc2.get(f"{_cp2}/api/capabilities/requests?{_q}")
                if _cr.status_code == 200:
                    _caps = _cr.json()
                    # Collect unique approved package names
                    _pkgs = sorted(set(
                        c.get("resource_name", "")
                        for c in _caps
                        if c.get("status") == "approved" and c.get("resource_name")
                    ))
                    if _pkgs:
                        pre_installed_packages = ",".join(_pkgs)
                        logger.info(
                            f"📦 Pre-installed packages for {task_id}: {pre_installed_packages}"
                        )
        except Exception as _cap_err:
            logger.warning(f"⚠️ Could not fetch capabilities: {_cap_err}")

        workspace_dir = os.path.join(workspaces_root, workspace_id)
        os.makedirs(workspace_dir, exist_ok=True)
        os.chmod(workspace_dir, 0o777)

        # --- Remove stale result.json from previous iteration ---
        # After a capability rebuild the old result.json still contains
        # capability_requested=true which causes an infinite loop if the
        # new container exits before writing fresh markers.
        _stale_result = os.path.join(workspace_dir, "result.json")
        if os.path.exists(_stale_result):
            try:
                os.remove(_stale_result)
                logger.info(f"🧹 Removed stale result.json from {workspace_dir}")
            except Exception as _rm_err:
                logger.warning(f"⚠️ Could not remove stale result.json: {_rm_err}")

        # --- service discovery for the agent container ---
        # Agent containers run on DinD's default bridge network with their own
        # network namespace — NOT network_mode="host".  They reach Compose
        # services via DinD's NAT (docker0 → eth0 → Compose bridge).
        #
        # We resolve all service endpoints here (the worker IS on the Compose
        # network) and inject them as explicit IP-based URLs.  This is the
        # same pattern used in distributed / cloud deployments where agents
        # discover services through injected configuration rather than
        # shared network namespaces.
        import socket as _socket

        def _resolve(name: str, fallback: str = "") -> str:
            """Resolve a Compose service name to an IP address."""
            try:
                return _socket.gethostbyname(name)
            except _socket.gaierror:
                logger.warning(f"⚠️ Could not resolve '{name}', using fallback '{fallback}'")
                return fallback or name

        control_plane_ip = os.getenv("CONTROL_PLANE_IP", "") or _resolve("control-plane")
        cp_url_for_agent = f"http://{control_plane_ip}:8000"
        llm_router_url = f"{cp_url_for_agent}/api/llm"

        # --- Zep CE memory service discovery ---
        zep_ip = os.getenv("ZEP_IP", "") or _resolve("zep", fallback="")
        zep_url_for_agent = f"http://{zep_ip}:8000" if zep_ip else ""
        # Session ID: DAG-scoped by node_id so memory persists across
        # retries for the same DAG node.  For standalone tasks we
        # fall back to the task_id.
        zep_session_id = ""
        if dag_id and node_id:
            zep_session_id = f"{dag_id}_{node_id}"
        elif dag_id:
            zep_session_id = f"{dag_id}_agent"
        else:
            zep_session_id = task_id

        # --- Dockerfile injection ---
        agent_dockerfile = ""
        agent_images_dir = os.getenv("AGENT_IMAGES_DIR", "/agent-images")
        dockerfile_path = os.path.join(agent_images_dir, task_id, "Dockerfile")
        if os.path.isfile(dockerfile_path):
            try:
                with open(dockerfile_path, "r") as _df:
                    agent_dockerfile = _df.read()
            except Exception:
                pass

        agent_env = {
            "TASK_ID": task_id,
            "ITERATION": str(iteration),
            "NODE_ID": node_id or "",  # For step segregation
            "CONTROL_PLANE_URL": cp_url_for_agent,
            "LLM_ROUTER_URL": llm_router_url,
            "OLLAMA_URL": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434"),
            "LLM_MODEL": llm_model,
            "TASK_DESCRIPTION": task_description[:2000],
            "AGENT_IMAGE": agent_image,
            "AGENT_DOCKERFILE": agent_dockerfile[:4000],
            "FOLLOW_UP": follow_up[:2000],
            "PRE_INSTALLED_PACKAGES": pre_installed_packages[:1000],
            "ZEP_URL": zep_url_for_agent,
            "ZEP_SESSION_ID": zep_session_id,
            "SKILL_INSTRUCTIONS": skill_instructions[:8000],
        }


        # Only the workspace is a bind mount. Adapters are baked into the agent
        # images (see Dockerfile.openclaw) — do NOT bind-mount them at their
        # image paths: `docker commit` records bind mounts as VOLUME entries, and
        # a VOLUME over a baked FILE (e.g. /opt/openclaw/taskforge-adapter.py)
        # makes any later `docker build FROM` the committed DAG image fail with
        # "cannot mount volume over existing file", breaking capability rebuilds.
        _agent_volumes = {workspace_dir: {"bind": "/workspace", "mode": "rw"}}

        container_kwargs = dict(
            image=agent_image,
            environment=agent_env,
            volumes=_agent_volumes,
            tmpfs={"/tmp": "size=100m,mode=1777"},
            read_only=True,
            detach=True,
        )

        if AGENT_SANDBOX_MODE == "gvisor":
            container_kwargs["runtime"] = "runsc"
            container_kwargs["privileged"] = False
            # Agent gets its own isolated network namespace on DinD's default
            # bridge (docker0).  Outbound traffic is NATed through DinD's
            # eth0 to the Compose network.  All service endpoints are
            # pre-resolved IPs — no DNS dependency inside the sandbox.
            # This pattern ports directly to VM / cloud isolation later.
        elif AGENT_SANDBOX_MODE == "insecure-dind":
            container_kwargs["privileged"] = True
            container_kwargs["network_mode"] = "host"
        else:
            raise ValueError(f"Unknown AGENT_SANDBOX_MODE: {AGENT_SANDBOX_MODE}")

        logger.info(f"🚀 Launching container (detached) with sandbox mode: {AGENT_SANDBOX_MODE} ...")
        try:
            container = docker_client.containers.run(**container_kwargs)
        except docker.errors.APIError as api_err:
            if "unknown or invalid runtime name: runsc" in str(api_err):
                logger.error(
                    "❌ gVisor runtime 'runsc' is not registered with Docker. "
                    "Install gVisor on the host — see docs/GVISOR_SETUP.md — "
                    "or set AGENT_SANDBOX_MODE=insecure-dind in your .env file."
                )
            raise

        logger.info(f"✅ Container started: {container.short_id}")
        return {
            "container_id": container.id,
            "workspace_dir": workspace_dir,
            "agent_image": agent_image,
            "image": agent_image,
            "status": "running",
            "sandbox_mode": AGENT_SANDBOX_MODE,
        }

    except Exception as e:
        logger.error(f"❌ Failed to start agent container: {e}", exc_info=True)
        return {"error": str(e)}


@activity.defn
async def poll_agent_turns(
    task_id: str,
    container_id: str,
    turns_seen: int,
) -> Dict[str, Any]:
    """Poll the LLM router for new agent turns and check if the container is still running.

    Returns ``{"container_done": bool, "new_turns": [...]}``.
    The workflow calls this in a loop, recording each turn via ``record_agent_turn``.
    """
    import docker
    import httpx

    docker_client = get_docker_client()
    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    new_turns = []
    container_done = False

    # Poll until the container exits, sending heartbeats to keep the activity alive.
    # Each poll cycle is ~3 seconds; we return to the workflow as soon as we have
    # new turns OR the container finishes.
    max_polls = 600  # ~30 min at 3s intervals
    # If the agent stops producing LLM turns but leaves a long-running process
    # (e.g. `node server.js`), the container never exits.  After this many
    # consecutive silent polls (~2 min), we force-stop the container so the
    # workflow can proceed.
    silent_polls = 0
    max_silent_polls = 40  # ~40 * 3s = 2 minutes of no new turns
    for _ in range(max_polls):
        activity.heartbeat(f"turns_seen={turns_seen + len(new_turns)}")

        # Check for new interactions from the LLM router
        batch_count = 0
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{cp_url}/api/llm/interactions/{task_id}",
                    params={"since": turns_seen + len(new_turns)},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    batch = data.get("interactions", [])
                    if batch:
                        batch_count = len(batch)
                        new_turns.extend(batch)
                        logger.info(f"📡 Got {batch_count} new turn(s) for {task_id} (total seen: {turns_seen + len(new_turns)})")
        except Exception as e:
            logger.warning(f"⚠️ Poll interactions failed: {e}")

        # Check if the agent posted a capability request to the control plane.
        # Some adapters (e.g. openclaw) POST directly but don't exit the
        # container, so we must detect it here and force-stop.
        try:
            async with httpx.AsyncClient(timeout=5.0) as cap_client:
                cap_resp = await cap_client.get(
                    f"{cp_url}/api/capabilities/requests",
                    params={"task_id": task_id, "status_filter": "pending"},
                )
                if cap_resp.status_code == 200:
                    pending_caps = cap_resp.json()
                    if pending_caps:
                        logger.info(
                            f"🔐 Capability request detected for {task_id} — "
                            f"force-stopping container {container_id[:12]}"
                        )
                        try:
                            c = docker_client.containers.get(container_id)
                            c.stop(timeout=5)
                        except Exception:
                            pass
                        container_done = True
                        break
        except Exception as cap_err:
            logger.debug(f"⚠️ Capability poll failed: {cap_err}")

        # Check container status
        try:
            container = docker_client.containers.get(container_id)
            status = container.status  # "running", "exited", "created", etc.
            if status != "running":
                container_done = True
                logger.info(f"🏁 Container {container_id[:12]} status: {status}")
        except docker.errors.NotFound:
            container_done = True
            logger.info(f"🏁 Container {container_id[:12]} not found (already removed)")
        except Exception as e:
            logger.warning(f"⚠️ Container status check failed: {e}")

        # Return to workflow if we have new turns to record or container is done
        if new_turns or container_done:
            break

        # Track silent polls — if agent hasn't produced any new LLM turns
        # for a long time, it likely started a server process.  Force-stop
        # the container so collect_agent_result can read the output.
        if batch_count == 0 and not container_done:
            silent_polls += 1
            if silent_polls >= max_silent_polls:
                logger.warning(
                    f"⏰ Container {container_id[:12]} silent for {silent_polls * 3}s — "
                    f"force-stopping (agent likely left a server running)"
                )
                try:
                    container = docker_client.containers.get(container_id)
                    container.stop(timeout=5)
                except Exception as stop_err:
                    logger.warning(f"⚠️ Failed to stop container: {stop_err}")
                container_done = True
                break
        else:
            silent_polls = 0

        await asyncio.sleep(3)

    return {
        "container_done": container_done,
        "new_turns": new_turns,
    }


@activity.defn
async def collect_agent_result(
    task_id: str,
    iteration: int,
    container_id: str,
    workspace_dir: str,
    agent_image: str,
    llm_model: str,
    dag_id: str = "",
) -> Dict[str, Any]:
    """Collect the final result from the stopped agent container.

    Reads the result from stdout markers or result.json, fetches any
    remaining LLM interactions, and cleans up the container.

    When ``dag_id`` is set the stopped container is committed to a new
    image before removal so that downstream DAG nodes inherit the
    file-system state (installed packages, generated files, etc.).
    """
    import docker
    import json as json_lib
    import httpx

    logger.info(f"📦 COLLECT_RESULT | Task: {task_id} | Iter: {iteration}")

    docker_client = get_docker_client()
    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    committed_image = ""

    try:
        container = docker_client.containers.get(container_id)

        # Wait for exit (should already be done, but just in case)
        exit_info = container.wait(timeout=120)
        exit_code = exit_info.get("StatusCode", -1)

        container_output = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        logger.info(f"📄 Container exited with code {exit_code}, output ({len(container_output)} bytes)")

        # Commit the container to a new image when running inside a DAG
        # so the next node inherits the full file-system state.
        if dag_id:
            try:
                short_task = task_id[:8]
                commit_tag = f"dag-{dag_id[:8]}-{short_task}"
                repo = "registry:5000/openclaw-agent"
                logger.info(f"📸 Committing container {container_id[:12]} as {repo}:{commit_tag}")
                container.commit(repository=repo, tag=commit_tag)
                committed_image = f"{repo}:{commit_tag}"
                # Push to registry so DinD can pull it for the next node
                docker_client.images.push(repo, tag=commit_tag)
                logger.info(f"✅ Committed DAG image pushed: {committed_image}")
            except Exception as commit_err:
                logger.warning(f"⚠️ Container commit failed: {commit_err}")

        # Clean up
        try:
            container.remove(force=True)
        except Exception:
            pass

    except docker.errors.NotFound:
        logger.warning(f"Container {container_id[:12]} already removed, reading result from file")
        container_output = ""
        exit_code = -1
    except Exception as e:
        logger.error(f"❌ Failed to collect container: {e}")
        return {"completed": False, "agent_failed": True, "error": str(e)}

    for line in container_output.split('\n')[:50]:
        if line.strip():
            logger.info(f"   {line}")

    # --- Extract result ---
    RESULT_START = "===OPENCLAW_RESULT_JSON_START==="
    RESULT_END = "===OPENCLAW_RESULT_JSON_END==="
    result = None

    if RESULT_START in container_output:
        try:
            start_idx = container_output.index(RESULT_START) + len(RESULT_START)
            end_idx = container_output.index(RESULT_END, start_idx)
            result_str = container_output[start_idx:end_idx].strip()
            result = json_lib.loads(result_str)
            logger.info("✅ Parsed result from stdout markers")
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse stdout markers: {e}")

    result_file = f"{workspace_dir}/result.json"
    if result is None and os.path.exists(result_file):
        try:
            with open(result_file, "r") as f:
                result = json_lib.load(f)
            logger.info(f"✅ Read result from file: {result_file}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to read result file: {e}")

    if result is not None:
        # If the agent didn't flag capability_requested in its result,
        # check the control plane for recent capability rows the agent may
        # have posted directly (e.g. openclaw adapter auto-request path).
        if not result.get("capability_requested"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as _cc:
                    _cr = await _cc.get(
                        f"{cp_url}/api/capabilities/requests",
                        params={"task_id": task_id},
                    )
                    if _cr.status_code == 200:
                        _requests = _cr.json()
                        _candidate = None
                        for row in _requests or []:
                            row_status = (row.get("status") or "").lower()
                            # Only consider PENDING as active capability request.
                            # "approved"/"modified" are already resolved and should not re-trigger.
                            if row_status == "pending":
                                _candidate = row
                                break
                        if _candidate:
                            row_status = (_candidate.get("status") or "").lower()
                            logger.info(
                                f"🔐 Detected control-plane capability row: "
                                f"{_candidate.get('capability_type')} / {_candidate.get('resource_name')} "
                                f"(status={row_status})"
                            )
                            result["capability_requested"] = True
                            result["capability"] = {
                                "type": _candidate.get("capability_type", "tool_install"),
                                "resource": _candidate.get("resource_name", ""),
                                "justification": _candidate.get("justification", ""),
                            }
                            result["capability_status"] = row_status
            except Exception:
                pass

        if not result.get("capability_requested"):
            marker_cap = _parse_capability_request_marker(
                ((result.get("deliverables") or {}).get("request.txt", ""))
                if isinstance(result.get("deliverables"), dict)
                else ""
            )
            if marker_cap:
                result["capability_requested"] = True
                result["capability"] = marker_cap
                result["capability_status"] = "artifact_marker"
                logger.info(
                    f"🔐 Detected capability marker from deliverables: "
                    f"{marker_cap.get('type')} / {marker_cap.get('resource')}"
                )

        if result.get("capability_requested"):
            cap = result.get("capability", {})
            logger.info(f"🔐 CAPABILITY | Task: {task_id} | Type: {cap.get('type')} | Resource: {cap.get('resource')}")
        elif result.get("completed"):
            logger.info(f"✅ COMPLETED | Task: {task_id}")
        else:
            logger.info(f"⏭️  CONTINUE | Task: {task_id}")

        # Fetch any remaining LLM interactions not yet seen by poll loop
        remaining_turns = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{cp_url}/api/llm/interactions/{task_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    all_interactions = data.get("interactions", [])
                    # The workflow knows how many it already recorded via turns_seen;
                    # we return ALL interactions and let the workflow diff.
                    remaining_turns = all_interactions
                    # Clear after fetching
                    await client.delete(f"{cp_url}/api/llm/interactions/{task_id}")
        except Exception as _e:
            logger.warning(f"⚠️ Could not fetch remaining interactions: {_e}")

        result["agent_logs"] = container_output[:10000]
        result["_temporal_metadata"] = {
            "task_id": task_id,
            "iteration": iteration,
            "image": agent_image,
            "timestamp": str(datetime.now()),
        }
        # Strip heavy payload from remaining turns to stay under Temporal's
        # 2 MB result size limit.  Only keep lightweight metadata.
        slim_turns = []
        for t in remaining_turns:
            slim = {
                "provider": t.get("provider"),
                "timestamp": t.get("timestamp"),
                "turn": t.get("turn"),
            }
            resp = t.get("response", {})
            slim["response"] = {
                "finish_reason": resp.get("finish_reason"),
                "content": (resp.get("content") or "")[:500],
                "tool_calls": [
                    {"name": tc.get("name")} for tc in resp.get("tool_calls", [])
                ],
                "usage": resp.get("usage"),
            }
            req = t.get("request", {})
            slim["request"] = {
                "msg_count": req.get("msg_count"),
                "tool_results": [{"tool_call_id": tr.get("tool_call_id")} for tr in req.get("tool_results", [])],
            }
            slim_turns.append(slim)
        result["_remaining_turns"] = slim_turns
        if committed_image:
            result["committed_image"] = committed_image
        return result

    # Fallback: no structured result — check control plane for pending
    # capability requests that the agent may have posted directly.
    logger.warning("⚠️ No result markers or file found, checking for pending capability requests")
    try:
        async with httpx.AsyncClient(timeout=10.0) as cap_client:
            cap_resp = await cap_client.get(
                f"{cp_url}/api/capabilities/requests",
                params={"task_id": task_id, "status_filter": "pending"},
            )
            if cap_resp.status_code == 200:
                pending_caps = cap_resp.json()
                if pending_caps:
                    cap = pending_caps[0]  # take the first pending request
                    logger.info(
                        f"🔐 Found pending capability request for {task_id}: "
                        f"{cap.get('capability_type')} / {cap.get('resource_name')}"
                    )
                    return {
                        "completed": False,
                        "capability_requested": True,
                        "capability": {
                            "type": cap.get("capability_type", "tool_install"),
                            "resource": cap.get("resource_name", ""),
                            "justification": cap.get("justification", ""),
                        },
                        "output": container_output[:10000],
                        "agent_logs": container_output[:10000],
                    }
    except Exception as cap_err:
        logger.warning(f"⚠️ Could not check capability requests: {cap_err}")

    error_msg = None
    if "ERROR:" in container_output or "Traceback" in container_output:
        lines = container_output.split('\n')
        for i, line in enumerate(lines):
            if "ERROR:" in line or "raise" in line:
                error_msg = '\n'.join(lines[i:min(i + 10, len(lines))])
                break

    return {
        "completed": False,
        "capability_requested": False,
        "output": container_output[:10000],
        "agent_logs": container_output[:10000],
        "parse_error": True,
        "error": error_msg[:500] if error_msg else "No result from agent (no markers, no file)",
    }


@activity.defn
async def record_agent_turn(
    task_id: str,
    iteration: int,
    turn_number: int,
    turn_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Record a single LLM turn as a visible Temporal activity.

    Each invocation appears as its own activity inside the ``AgentStepWorkflow``
    child workflow, giving operators per-turn visibility.
    """
    provider = turn_data.get("provider", "unknown")
    timestamp = turn_data.get("timestamp", "")

    req = turn_data.get("request", {})
    resp = turn_data.get("response", {})

    msg_count = req.get("msg_count", 0)
    tool_results_in = req.get("tool_results", [])

    finish_reason = resp.get("finish_reason", "")
    tool_calls = resp.get("tool_calls", [])
    usage = resp.get("usage", {})
    content_preview = (resp.get("content") or "")[:300]

    tool_names = [tc.get("name", "?") for tc in tool_calls]
    if tool_calls:
        action_desc = f"Tool calls: {', '.join(tool_names)}"
    elif content_preview:
        action_desc = f"Response: {content_preview[:120]}..."
    else:
        action_desc = f"Finish: {finish_reason}"

    logger.info(
        f"📋 TURN {turn_number} | Task: {task_id} | Iter: {iteration} | "
        f"Provider: {provider} | Msgs: {msg_count} | "
        f"Tool results in: {len(tool_results_in)} | "
        f"Tool calls out: {len(tool_calls)} | {action_desc[:100]}"
    )

    if usage:
        logger.info(
            f"   └─ Tokens: in={usage.get('input_tokens', '?')} "
            f"out={usage.get('output_tokens', '?')} "
            f"total={usage.get('total_tokens', '?')}"
        )

    for tc in tool_calls:
        args = tc.get("arguments", {})
        name = tc.get("name", "?")
        if isinstance(args, dict):
            if name.lower() in ("write", "write_file", "writefile"):
                fpath = args.get("file_path", args.get("path", "?"))
                size = len(args.get("content", args.get("file_text", "")))
                logger.info(f"   └─ 📝 Write: {fpath} ({size} chars)")
            elif name.lower() in ("exec", "bash", "execute", "run"):
                cmd = str(args.get("command", args.get("cmd", "?")))
                logger.info(f"   └─ ⚡ Exec: {cmd[:120]}")
            elif name.lower() in ("read", "read_file", "readfile"):
                fpath = args.get("file_path", args.get("path", "?"))
                logger.info(f"   └─ 📖 Read: {fpath}")
            else:
                logger.info(f"   └─ 🔧 {name}: {str(args)[:120]}")

    return {
        "task_id": task_id,
        "iteration": iteration,
        "turn": turn_number,
        "provider": provider,
        "finish_reason": finish_reason,
        "tool_calls": tool_names,
        "tool_results_received": len(tool_results_in),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "timestamp": timestamp,
    }


@activity.defn
async def store_task_output(
    task_id: str,
    iteration: int,
    result: Dict[str, Any],
    image_used: str,
    model_used: str,
) -> Dict[str, Any]:
    """Store agent step output in the control-plane database."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    # Extract duration from OpenClaw meta if available
    duration_ms = None
    output_str = result.get("output", "")
    if isinstance(output_str, str) and '"durationMs"' in output_str:
        import re
        m = re.search(r'"durationMs"\s*:\s*(\d+)', output_str)
        if m:
            duration_ms = int(m.group(1))

    # Extract deliverables (files created by the agent)
    deliverables = result.get("deliverables")
    compact_summary = _build_compact_step_summary(
        {
            "status": "completed" if str(result.get("completed", "")).lower() == "true" else "running",
            "completed": result.get("completed", False),
            "deliverables": deliverables,
            "error": result.get("error"),
            "output": output_str,
        }
    )

    # Keep raw result for audit, but store compact summary for chat/task readability.
    result["compact_summary"] = compact_summary

    payload = {
        "task_id": task_id,
        "iteration": iteration,
        "completed": str(result.get("completed", False)).lower(),
        "capability_requested": str(result.get("capability_requested", False)).lower(),
        "agent_logs": result.get("agent_logs", "")[:50000],
        "output": output_str[:50000] if isinstance(output_str, str) else str(output_str)[:50000],
        "error": result.get("error"),
        "llm_response_preview": compact_summary[:500],
        "model_used": model_used,
        "image_used": image_used,
        "duration_ms": duration_ms,
        "deliverables": deliverables,
        "raw_result": result,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/tasks/{task_id}/outputs",
                json=payload,
            )
            resp.raise_for_status()
            logger.info(f"📦 OUTPUT stored | Task: {task_id} | Iteration: {iteration}")
            return resp.json()
    except Exception as e:
        logger.warning(f"⚠️ Failed to store output: {e}")
        return {"error": str(e)}


@activity.defn
async def get_last_iteration(task_id: str) -> int:
    """Get the last iteration number for a task so continuations don't overlap."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}/outputs")
            if resp.status_code == 200:
                data = resp.json()
                outputs = data.get("outputs", [])
                if outputs:
                    max_iter = max(o.get("iteration", 0) for o in outputs)
                    logger.info(f"📊 Last iteration for {task_id}: {max_iter}")
                    return max_iter
    except Exception as e:
        logger.warning(f"⚠️ Could not fetch last iteration: {e}")

    return 0


@activity.defn
async def create_capability_request(
    task_id: str,
    capability: Dict[str, Any]
) -> Dict[str, Any]:
    """Create capability request in control plane"""
    import httpx
    
    logger.info(f"📋 CAPABILITY_REQUEST | Task: {task_id} | Type: {capability.get('type')} | Resource: {capability.get('resource')}")
    logger.info(f"   └─ Justification: {capability.get('justification')}")
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{control_plane_url}/api/capabilities/requests",
                json={
                    "task_id": task_id,
                    "capability_type": capability.get("type", "tool_install"),
                    "resource_name": capability.get("resource", "unknown"),
                    "justification": capability.get("justification", "Requested by agent"),
                    "details": capability
                },
                timeout=10.0
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"Capability request created: {result}")
            return result
    except Exception as e:
        logger.error(f"Failed to create capability request: {e}")
        return {"request_id": None, "error": str(e)}


@activity.defn
async def list_task_capability_requests(task_id: str) -> List[Dict[str, Any]]:
    """List capability requests for a task, newest first."""
    import httpx

    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{cp_url}/api/capabilities/requests",
                params={"task_id": task_id},
            )
            resp.raise_for_status()
            rows = resp.json()
            if isinstance(rows, list):
                return rows
    except Exception as e:
        logger.warning(f"⚠️ Failed to list capability requests for {task_id}: {e}")
    return []


@activity.defn
async def dismiss_pending_capabilities(task_id: str) -> Dict[str, Any]:
    """Dismiss all pending capability requests for a task via the control plane.

    Called after a capability has been processed (approved + image built, or
    duplicate-skipped) so that ``poll_agent_turns`` won't kill the next
    container because of stale pending rows.
    """
    import httpx

    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{cp_url}/api/capabilities/requests/dismiss-pending",
                params={"task_id": task_id},
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"🧹 Dismissed pending caps for {task_id}: {result}")
            return result
    except Exception as e:
        logger.warning(f"⚠️ Failed to dismiss pending caps for {task_id}: {e}")
        return {"dismissed": 0, "error": str(e)}


@activity.defn
async def check_verdict_guard(task_id: str) -> Dict[str, Any]:
    """Return latest immutable verdict for a task, if available."""
    import httpx

    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{cp_url}/api/tasks/{task_id}/verdict")
            if resp.status_code == 404:
                return {"verdict": None}
            resp.raise_for_status()
            data = resp.json() or {}
            verdict = (data.get("verdict") or "").strip().lower() or None
            return {
                "verdict": verdict,
                "submitted_at": data.get("submitted_at") or data.get("created_at"),
                "task_id": task_id,
            }
    except Exception as e:
        logger.debug(f"check_verdict_guard failed for {task_id}: {e}")
        return {"verdict": None, "error": str(e)}


# Known npm packages / patterns for auto-detecting package type from generic tool_install
_NPM_KNOWN_PACKAGES = {
    "agent-browser", "express", "typescript", "ts-node", "prettier", "eslint",
    "webpack", "vite", "tailwindcss", "postcss", "autoprefixer", "react",
    "react-dom", "leaflet", "react-leaflet", "recharts", "puppeteer",
    "playwright", "cypress",
}
_APT_PATTERNS = ("-dev", "lib", "build-essential", "cmake", "gcc", "g++",
                 "make", "pkg-config", "libssl", "libcurl", "zlib")


def _detect_package_type(cap_type: str, package_name: str) -> str:
    """Detect the build capability type for a package.

    When the agent emits a generic 'tool_install' capability, we need to
    determine whether this is a pip, npm, or apt package.
    """
    if cap_type not in ("tool_install",):
        return cap_type
    # Check known npm packages
    if package_name in _NPM_KNOWN_PACKAGES:
        return "npm_package"
    # Scoped npm packages (e.g. @scope/pkg)
    if package_name.startswith("@"):
        return "npm_package"
    # APT patterns
    if any(package_name.startswith(p) or package_name.endswith(p) for p in _APT_PATTERNS):
        return "apt_package"
    # Default to pip
    return "pip_package"


@activity.defn
async def build_agent_image(
    task_id: str,
    capability: Dict[str, Any],
    current_image: str = "localhost:5000/openclaw-agent:openclaw"
) -> Dict[str, Any]:
    """Build new agent image with capability.
    
    Uses current_image as the base so capabilities accumulate
    incrementally: base → v1 (+ redis) → v2 (+ flask) → v3 ...

    Returns a dict:
      - image: the new (or fallback) image tag
      - feedback: supply-chain feedback string to inject into agent context
                  (empty string if everything was approved)
      - denied: list of denied package dicts (empty if none)
    """
    import httpx
    
    cap_type = capability.get("type", "tool_install")
    resource = capability.get("resource", "")
    logger.info(f"🔨 BUILD_IMAGE | Task: {task_id} | Adding capability: {cap_type}:{resource}")
    
    image_builder_url = os.getenv("IMAGE_BUILDER_URL", "http://openclaw-image-builder:8002")
    
    try:
        # Map capability to build capability format
        # Split comma-separated resources into individual capabilities.
        #
        # If the capability request carries per-package type info in
        # details.packages (new format), use it directly instead of
        # guessing via _detect_package_type.
        resources = [r.strip() for r in resource.split(",") if r.strip()]
        details_packages = capability.get("details", {}).get("packages") if isinstance(capability.get("details"), dict) else None

        # Build a lookup from package name → explicit type when available.
        explicit_types: Dict[str, str] = {}
        if details_packages and isinstance(details_packages, list):
            for entry in details_packages:
                if isinstance(entry, dict) and "name" in entry and "type" in entry:
                    explicit_types[entry["name"]] = entry["type"]

        build_capabilities = []
        for r in resources:
            # Strip version suffix (e.g. "pandas==2.0.1" → "pandas") for lookup
            bare_name = r.split("==")[0].strip()
            if bare_name in explicit_types:
                pkg_type = explicit_types[bare_name]
                logger.info(f"   📦 {bare_name} → {pkg_type} (from details.packages)")
            else:
                pkg_type = _detect_package_type(cap_type, bare_name)
                logger.info(f"   📦 {bare_name} → {pkg_type} (heuristic fallback)")
            build_capabilities.append({
                "type": pkg_type,
                "name": r,
                "version": None,
            })
        
        # Convert current_image to registry:5000 format for docker-dind
        base_image = current_image.replace("localhost:5000/", "registry:5000/")
        
        logger.info(f"   └─ Building FROM {base_image} (incremental)")
        logger.info(f"   └─ Adding: {resources}")
        
        # Call image builder service — retry once on transient network errors
        async with httpx.AsyncClient(timeout=300.0) as client:
            last_err = None
            for attempt in range(2):
                try:
                    response = await client.post(
                        f"{image_builder_url}/build",
                        json={
                            "task_id": task_id,
                            "base_image": base_image,
                            "capabilities": build_capabilities
                        }
                    )
                    response.raise_for_status()
                    result = response.json()
                    break  # success
                except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as net_err:
                    last_err = net_err
                    if attempt == 0:
                        logger.warning(f"   └─ POST /build failed ({type(net_err).__name__}), retrying in 2s...")
                        await asyncio.sleep(2)
                    else:
                        raise
            else:
                raise last_err  # type: ignore[misc]

            # ── Check for supply-chain denial ────────────────────────────
            supply_chain_feedback = result.get("supply_chain_feedback", "") or ""
            supply_chain_denied = result.get("supply_chain_denied") or []

            # If the build was entirely denied (status="denied"), return
            # the current image + feedback immediately — no polling needed.
            if result.get("status") == "denied":
                logger.warning(f"🚫 BUILD_DENIED (supply chain) | Task: {task_id} | "
                               f"Denied: {[d.get('name') for d in supply_chain_denied]}")
                return {
                    "image": current_image,
                    "feedback": supply_chain_feedback,
                    "denied": supply_chain_denied,
                }
            
            build_id = result["build_id"]
            expected_tag = result["image_tag"]
            logger.info(f"   └─ Build started | Build ID: {build_id} | Target: {expected_tag}")
            
            # Poll for build completion
            max_wait = 600  # 10 minutes
            poll_interval = 5
            waited = 0
            
            while waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                
                status_response = await client.get(f"{image_builder_url}/builds/{build_id}")
                status_response.raise_for_status()
                status = status_response.json()
                
                if status["status"] == "success":
                    image_tag = status["image_tag"]
                    # Convert registry network name to localhost for worker access
                    if image_tag.startswith("registry:5000/"):
                        image_tag = image_tag.replace("registry:5000/", "localhost:5000/")
                    logger.info(f"✅ BUILD_SUCCESS | Task: {task_id} | Image: {image_tag} | Build time: {waited}s")
                    logger.info(f"   └─ Dockerfile saved to: agent-images/{task_id}/")
                    return {
                        "image": image_tag,
                        "feedback": supply_chain_feedback,
                        "denied": supply_chain_denied,
                    }
                elif status["status"] == "failed":
                    error = status.get("error", "Unknown error")
                    logger.error(f"❌ BUILD_FAILED | Task: {task_id} | Error: {error}")
                    raise Exception(f"Image build failed: {error}")
                elif waited % 15 == 0:  # Log every 15 seconds
                    logger.info(f"   └─ Build in progress... ({waited}s elapsed)")
            
            raise Exception("Build timeout after 10 minutes")
            
    except Exception as e:
        import traceback
        logger.error(f"❌ BUILD_ERROR | Task: {task_id} | {type(e).__name__}: {repr(e)}")
        logger.error(f"   └─ Traceback:\n{traceback.format_exc()}")
        logger.warning(f"⚠️  FALLBACK | Task: {task_id} | Continuing with previous image: {current_image}")
        # Fall back to the image we already had — include build error as feedback
        return {
            "image": current_image,
            "feedback": (
                f"CAPABILITY_BUILD_FAILED: Could not install '{resource}'. "
                f"Error: {str(e)[:300]}. "
                "The package may not be available for this platform. "
                "Find an alternative approach."
            ),
            "denied": [],
        }


@activity.defn
async def update_task_policy(
    task_id: str,
    capability: Dict[str, Any],
    new_image: str
) -> Dict[str, Any]:
    """Update task policy and persist the new image tag in the DB.

    After a capability build, the rebuilt image (e.g. task-xxx-v2) must be
    persisted so that continuation workflows pick it up instead of falling
    back to the bare base image.
    """
    import httpx

    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    # Persist the new image on the task record
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{control_plane_url}/api/tasks/{task_id}/image",
                json={"current_image": new_image},
            )
            resp.raise_for_status()
            logger.info(f"✅ TASK_IMAGE_UPDATED | Task: {task_id} | Image: {new_image}")
    except Exception as e:
        logger.error(f"❌ Failed to persist image for task {task_id}: {e}")

    return {"updated": True, "new_image": new_image}


@activity.defn
async def update_task_status(task_id: str, status: str) -> Dict[str, Any]:
    """Update task status in control-plane DB for real-time frontend sync."""
    import httpx

    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{control_plane_url}/api/tasks/{task_id}/status",
                json={"status": status},
            )
            resp.raise_for_status()
            logger.info(f"📊 TASK_STATUS_UPDATED | Task: {task_id} | Status: {status}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to update task status for {task_id}: {e}")
    
    return {"updated": True, "status": status}


@activity.defn
async def add_to_supply_chain(
    task_id: str,
    capability: Dict[str, Any],
    denied_names: List[str],
) -> Dict[str, Any]:
    """Add approved (non-denied) packages from a capability request to the supply chain allowlist."""
    import httpx

    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    cap_type = capability.get("type", "tool_install")
    resource = capability.get("resource", "")

    # Map capability type to package manager
    manager_map = {
        "pip_package": "pip",
        "apt_package": "apt",
        "apk_package": "apk",
        "npm_package": "npm",
    }

    # For generic tool_install, auto-detect the manager per-package
    # using the same logic as build_agent_image.
    # First, try to get typed info from details.packages (new format).
    details_packages = capability.get("details", {}).get("packages") if isinstance(capability.get("details"), dict) else None
    explicit_types: Dict[str, str] = {}
    if details_packages and isinstance(details_packages, list):
        for entry in details_packages:
            if isinstance(entry, dict) and "name" in entry and "type" in entry:
                explicit_types[entry["name"]] = entry["type"]

    if cap_type == "tool_install" and not explicit_types:
        # Detect based on first package (all resources in one request
        # are typically the same type)
        first_pkg = [r.strip() for r in resource.split(",") if r.strip()]
        if first_pkg:
            detected = _detect_package_type(cap_type, first_pkg[0])
            manager = manager_map.get(detected, "pip")
        else:
            manager = "pip"
    elif not explicit_types:
        manager = manager_map.get(cap_type, "pip")

    # Split comma-separated resources, exclude denied ones
    denied_set = set(denied_names)
    packages = [r.strip() for r in resource.split(",") if r.strip()]
    approved = [p for p in packages if p not in denied_set]

    if not approved:
        logger.info("📦 No approved packages to add to supply chain")
        return {"added": 0, "skipped": 0}

    # Detect image type from the task's current image or DAG base_image
    image_type = "openclaw"  # default fallback
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
            if resp.status_code == 200:
                task_data = resp.json()
                # First try current_image tag
                cur_img = task_data.get("current_image", "")
                tag = cur_img.rsplit(":", 1)[-1] if ":" in cur_img else ""
                known_types = await _fetch_known_image_types()
                for known in known_types:
                    if known in tag:
                        image_type = known
                        break
                else:
                    # Tag is a DAG-specific tag like "dag-dag-745a-task-ddb",
                    # try the DAG's base_image field instead
                    dag_id = task_data.get("dag_id", "")
                    if dag_id:
                        dag_resp = await client.get(f"{control_plane_url}/api/dags/{dag_id}")
                        if dag_resp.status_code == 200:
                            dag_data = dag_resp.json()
                            base = dag_data.get("base_image", "")
                            for known in known_types:
                                if known in base:
                                    image_type = known
                                    break
    except Exception as e:
        logger.warning(f"📦 Could not detect image type for {task_id}: {e}")

    logger.info(f"📦 Adding to supply chain for image_type={image_type}: {approved}")

    added = 0
    skipped = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for pkg in approved:
            # Resolve per-package manager from explicit types if available
            bare_name = pkg.split("==")[0].strip()
            if bare_name in explicit_types:
                pkg_manager = manager_map.get(explicit_types[bare_name], "pip")
            elif explicit_types:
                # Have explicit types but this package isn't in the map — heuristic
                pkg_manager = manager_map.get(_detect_package_type(cap_type, bare_name), "pip")
            else:
                pkg_manager = manager  # single manager for whole request (legacy)

            try:
                resp = await client.post(
                    f"{control_plane_url}/api/supply-chain/packages",
                    json={
                        "image_type": image_type,
                        "manager": pkg_manager,
                        "package_name": pkg,
                        "notes": f"Auto-added from approved capability request",
                        "is_exception": False,
                    },
                )
                if resp.status_code == 201:
                    added += 1
                    logger.info(f"📦 Supply chain: added '{pkg}' to {image_type}/{pkg_manager}")
                elif resp.status_code == 409:
                    skipped += 1  # already exists
                else:
                    logger.warning(f"📦 Supply chain: unexpected status {resp.status_code} for '{pkg}'")
            except Exception as e:
                logger.error(f"📦 Supply chain: failed to add '{pkg}': {e}")

    logger.info(f"📦 Supply chain update: {added} added, {skipped} already existed")
    return {"added": added, "skipped": skipped}


@activity.defn
async def reload_supply_chain() -> Dict[str, Any]:
    """Tell the image-builder to hot-reload its supply-chain config."""
    import httpx

    image_builder_url = os.getenv("IMAGE_BUILDER_URL", "http://openclaw-image-builder:8002")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{image_builder_url}/supply-chain/reload")
            resp.raise_for_status()
            logger.info("🔄 Supply chain reloaded on image-builder")
            return resp.json()
    except Exception as e:
        logger.warning(f"🔄 Supply chain reload failed (non-fatal): {e}")
        return {"status": "failed", "error": str(e)}


@activity.defn
async def finalize_task(task_id: str, final_status: str = "completed") -> Dict[str, Any]:
    """Finalize task execution - update status in control plane"""
    import httpx
    
    logger.info(f"🏁 FINALIZE | Task: {task_id} | Status: {final_status} | Collecting results and cleaning up")
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    # Update task status via control-plane API
    endpoint = "complete" if final_status == "completed" else "fail"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/tasks/{task_id}/{endpoint}"
            )
            resp.raise_for_status()
            logger.info(f"✅ FINALIZE | Task: {task_id} | Status updated to {final_status}")
    except Exception as e:
        logger.error(f"❌ FINALIZE | Task: {task_id} | Failed to update status: {e}")
    
    return {
        "task_id": task_id,
        "status": final_status,
        "outputs": {}
    }


def _normalize_deployment_entrypoint(entrypoint: str, port: int) -> str:
    """Ensure the entrypoint binds to 0.0.0.0:{port} so it's reachable outside the container.

    Common servers default to 127.0.0.1 which is unreachable from the Docker host.
    This injects the correct bind flags when they are missing.
    """
    if "gunicorn" in entrypoint and "-b" not in entrypoint and "--bind" not in entrypoint:
        # Insert -b 0.0.0.0:PORT right after 'gunicorn'
        entrypoint = entrypoint.replace("gunicorn", f"gunicorn -b 0.0.0.0:{port}", 1)
    elif "uvicorn" in entrypoint and "--host" not in entrypoint:
        entrypoint += f" --host 0.0.0.0 --port {port}"
    elif "flask run" in entrypoint and "--host" not in entrypoint:
        entrypoint += f" --host 0.0.0.0 --port {port}"
    return entrypoint


@activity.defn
async def create_deployment(task_id: str, deployment: Dict[str, Any]) -> Dict[str, Any]:
    """Create a deployment record in the control plane."""
    import httpx
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    payload = {
        "task_id": task_id,
        "name": deployment.get("name", f"deploy-{task_id}"),
        "entrypoint": deployment.get("entrypoint", "python app.py"),
        "port": deployment.get("port", 5000),
        "files": deployment.get("files"),
        "agent_image": deployment.get("agent_image"),
    }
    
    logger.info(f"📦 CREATE_DEPLOYMENT | Task: {task_id} | Name: {payload['name']} | Port: {payload['port']}")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/deployments",
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"✅ Deployment created: {result.get('id')} | Status: {result.get('status')}")
            return result
    except Exception as e:
        logger.error(f"❌ Failed to create deployment: {e}")
        return {"error": str(e)}


@activity.defn
async def check_deploy_authority(task_id: str) -> Dict[str, Any]:
    """Check whether a task (DAG node) is authorized to deploy.

    For DAG nodes, checks the 'deploy_authorized' flag in the node config.
    For standalone tasks, always allows deployment.
    Returns {can_deploy, reason}.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
        if resp.status_code != 200:
            return {"can_deploy": True, "reason": "standalone_task"}

        task_data = resp.json()
        dag_id = task_data.get("dag_id")
        node_id = task_data.get("node_id")

        if not dag_id or not node_id:
            return {"can_deploy": True, "reason": "standalone_task"}

        # Fetch DAG nodes to check deploy_authorized flag
        nodes_resp = await client.get(f"{control_plane_url}/api/dags/{dag_id}/nodes")
        if nodes_resp.status_code == 200:
            nodes = nodes_resp.json()
            for node in nodes:
                if node.get("node_id") == node_id:
                    config = node.get("config", {})
                    if config.get("deploy_authorized", False):
                        return {"can_deploy": True, "reason": "deploy_authorized"}
                    else:
                        return {"can_deploy": False, "reason": "node_not_authorized"}

        return {"can_deploy": False, "reason": "node_not_found"}


@activity.defn
async def build_deployment_image(deployment_id: str) -> Dict[str, Any]:
    """Build a minimal deployment image (no OpenClaw, just app + deps)."""
    import httpx
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    image_builder_url = os.getenv("IMAGE_BUILDER_URL", "http://openclaw-image-builder:8002")
    
    logger.info(f"🔨 BUILD_DEPLOYMENT | Deployment: {deployment_id}")
    
    try:
        # Fetch deployment details
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{control_plane_url}/api/deployments/{deployment_id}")
            resp.raise_for_status()
            deployment = resp.json()
        
        task_id = deployment["task_id"]
        
        # Update status to building
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.patch(
                f"{control_plane_url}/api/deployments/{deployment_id}",
                json={"status": "building"},
            )
        
        # Build via image-builder
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{image_builder_url}/build-deployment",
                json={
                    "deployment_id": deployment_id,
                    "task_id": task_id,
                    "entrypoint": deployment.get("entrypoint", "python app.py"),
                    "port": deployment.get("port", 5000),
                    "agent_image": deployment.get("agent_image"),
                }
            )
            resp.raise_for_status()
            result = resp.json()
            
            build_id = result["build_id"]
            logger.info(f"   └─ Build started | Build ID: {build_id}")
            
            # Poll for completion
            max_wait = 300
            waited = 0
            while waited < max_wait:
                await asyncio.sleep(5)
                waited += 5
                status_resp = await client.get(f"{image_builder_url}/builds/{build_id}")
                status_resp.raise_for_status()
                status = status_resp.json()
                
                if status["status"] == "success":
                    image_tag = status["image_tag"]
                    logger.info(f"✅ DEPLOYMENT_IMAGE_BUILT | {deployment_id} | Image: {image_tag}")
                    
                    # Update deployment record
                    async with httpx.AsyncClient(timeout=15.0) as cp_client:
                        await cp_client.patch(
                            f"{control_plane_url}/api/deployments/{deployment_id}",
                            json={"image_tag": image_tag, "status": "built"},
                        )
                    return {"image_tag": image_tag, "status": "built"}
                elif status["status"] == "failed":
                    raise Exception(f"Build failed: {status.get('error')}")
            
            raise Exception("Build timeout")
    except Exception as e:
        logger.error(f"❌ DEPLOYMENT_BUILD_FAILED | {deployment_id} | {e}")
        # Mark deployment as failed
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.patch(
                    f"{control_plane_url}/api/deployments/{deployment_id}",
                    json={"status": "failed", "error": str(e)},
                )
        except Exception:
            pass
        return {"error": str(e), "status": "failed"}


@activity.defn
async def start_deployment_container(deployment_id: str) -> Dict[str, Any]:
    """Start a deployment container."""
    import docker
    import httpx
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    logger.info(f"▶️  START_DEPLOYMENT | {deployment_id}")
    
    try:
        # Fetch deployment details
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{control_plane_url}/api/deployments/{deployment_id}")
            resp.raise_for_status()
            deployment = resp.json()
        
        image_tag = deployment["image_tag"]
        port = deployment.get("port", 5000)
        
        if not image_tag:
            raise Exception("No image_tag on deployment — not built yet?")
        
        docker_client = get_docker_client()
        
        # Pull image if needed
        try:
            docker_client.images.get(image_tag)
        except docker.errors.ImageNotFound:
            pull_tag = image_tag.replace("localhost:5000", "registry:5000")
            docker_client.images.pull(pull_tag)
            image_tag = pull_tag
        
        # Find an available host port in the 9100-9120 range
        # These ports are exposed from DinD to the host machine
        used_ports = set()
        for c in docker_client.containers.list(all=True):
            ports_map = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for bindings in ports_map.values():
                if bindings:
                    for b in bindings:
                        try:
                            used_ports.add(int(b["HostPort"]))
                        except (KeyError, ValueError, TypeError):
                            pass
        
        host_port = None
        for p in range(9100, 9121):
            if p not in used_ports:
                host_port = p
                break
        
        if host_port is None:
            raise Exception("No available ports in range 9100-9120 — too many deployments running")

        # Run with explicit port mapping (port is forwarded through DinD to host)
        container = docker_client.containers.run(
            image_tag,
            detach=True,
            name=f"deploy-{deployment_id}",
            ports={f"{port}/tcp": host_port},
            restart_policy={"Name": "unless-stopped"},
            labels={
                "openclaw.deployment": deployment_id,
                "openclaw.task": deployment.get("task_id", ""),
            },
        )
        
        url = f"http://localhost:{host_port}" if host_port else None
        
        logger.info(f"✅ DEPLOYMENT_STARTED | {deployment_id} | Container: {container.short_id} | Port: {host_port}")
        
        # Update deployment record
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.patch(
                f"{control_plane_url}/api/deployments/{deployment_id}",
                json={
                    "status": "running",
                    "container_id": container.id,
                    "host_port": host_port,
                    "url": url,
                },
            )
        
        return {"container_id": container.id, "host_port": host_port, "url": url}
    
    except Exception as e:
        logger.error(f"❌ DEPLOYMENT_START_FAILED | {deployment_id} | {e}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.patch(
                    f"{control_plane_url}/api/deployments/{deployment_id}",
                    json={"status": "failed", "error": str(e)},
                )
        except Exception:
            pass
        return {"error": str(e)}


@activity.defn
async def trial_deploy(
    task_id: str,
    deployment: Dict[str, Any],
) -> Dict[str, Any]:
    """Trial deployment — build image, start ephemeral container, health-check.

    Returns:
      - On success: {passed: True, image_tag: "...", logs: "..."}
      - On failure: {passed: False, error: "...", phase: "build|start|health", logs: "..."}
    """
    import docker
    import httpx

    image_builder_url = os.getenv("IMAGE_BUILDER_URL", "http://openclaw-image-builder:8002")

    trial_id = f"trial-{task_id[-8:]}"
    entrypoint = deployment.get("entrypoint", "python app.py")
    port = deployment.get("port", 5000)
    container = None
    image_tag = None

    logger.info(f"🧪 TRIAL_DEPLOY | Task: {task_id} | Port: {port} | Entrypoint: {entrypoint}")

    try:
        # Phase 1: Build the deployment image via image-builder
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{image_builder_url}/build-deployment",
                json={
                    "deployment_id": trial_id,
                    "task_id": task_id,
                    "entrypoint": entrypoint,
                    "port": port,
                    "agent_image": deployment.get("agent_image"),
                },
            )
            resp.raise_for_status()
            result = resp.json()
            build_id = result["build_id"]
            logger.info(f"🧪 Trial build started | Build ID: {build_id}")

            # Poll for build completion
            max_wait = 300
            waited = 0
            while waited < max_wait:
                await asyncio.sleep(5)
                waited += 5
                status_resp = await client.get(f"{image_builder_url}/builds/{build_id}")
                status_resp.raise_for_status()
                status = status_resp.json()

                if status["status"] == "success":
                    image_tag = status["image_tag"]
                    logger.info(f"🧪 Trial image built: {image_tag}")
                    break
                elif status["status"] == "failed":
                    error = status.get("error", "Unknown build error")
                    logger.warning(f"🧪 TRIAL_FAILED (build) | {task_id} | {error}")
                    return {"passed": False, "error": error, "phase": "build", "logs": ""}
            else:
                return {"passed": False, "error": "Build timed out after 5 minutes", "phase": "build", "logs": ""}

        # Phase 2: Start ephemeral container
        docker_client = get_docker_client()

        # Pull image if needed
        pull_tag = image_tag
        try:
            docker_client.images.get(image_tag)
        except Exception:
            pull_tag = image_tag.replace("localhost:5000", "registry:5000")
            docker_client.images.pull(pull_tag)

        # Use ephemeral port range 9200-9220 for trials (separate from real 9100-9120)
        used_ports = set()
        for c in docker_client.containers.list(all=True):
            ports_map = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for bindings in ports_map.values():
                if bindings:
                    for b in bindings:
                        try:
                            used_ports.add(int(b["HostPort"]))
                        except (KeyError, ValueError, TypeError):
                            pass

        host_port = None
        for p in range(9200, 9221):
            if p not in used_ports:
                host_port = p
                break

        if host_port is None:
            return {"passed": False, "error": "No available ports for trial (9200-9220)", "phase": "start", "logs": ""}

        container_name = f"trial-{task_id[-12:]}"
        # Remove any stale trial container with same name
        try:
            old = docker_client.containers.get(container_name)
            old.remove(force=True)
        except Exception:
            pass

        container = docker_client.containers.run(
            pull_tag,
            detach=True,
            name=container_name,
            ports={f"{port}/tcp": host_port},
            labels={"openclaw.trial": "true", "openclaw.task": task_id},
        )
        logger.info(f"🧪 Trial container started: {container.short_id} on port {host_port}")

        # Phase 3: Health check — wait for app to start, then smoke-test functionality
        health_passed = False
        smoke_errors = []
        startup_wait = 15  # seconds to wait for app startup
        check_attempts = 6  # try 6 times, 5s apart = 30s total
        base_url = f"http://docker-dind:{host_port}"

        await asyncio.sleep(startup_wait)

        # 3a: Check container is still running (not crash-looping)
        try:
            container.reload()
            cstate = container.status
            if cstate != "running":
                early_logs = container.logs(tail=30).decode("utf-8", errors="replace")
                return {
                    "passed": False,
                    "error": f"Container exited during startup (status: {cstate}). Check logs for errors.",
                    "phase": "health",
                    "logs": early_logs[:2000],
                    "image_tag": image_tag,
                }
        except Exception:
            pass

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 3b: Wait for port to respond
            main_resp = None
            for attempt in range(check_attempts):
                try:
                    resp = await client.get(f"{base_url}/")
                    if resp.status_code < 500:
                        main_resp = resp
                        health_passed = True
                        logger.info(f"🧪 Port responding | Status: {resp.status_code}")
                        break
                except Exception:
                    pass

                # Re-check container status each attempt
                try:
                    container.reload()
                    if container.status != "running":
                        early_logs = container.logs(tail=30).decode("utf-8", errors="replace")
                        return {
                            "passed": False,
                            "error": f"Container crashed during health check (status: {container.status}). Check logs.",
                            "phase": "health",
                            "logs": early_logs[:2000],
                            "image_tag": image_tag,
                        }
                except Exception:
                    pass

                if attempt < check_attempts - 1:
                    await asyncio.sleep(5)

            if not health_passed:
                try:
                    logs = container.logs(tail=50).decode("utf-8", errors="replace")
                except Exception:
                    logs = ""
                logger.warning(f"🧪 ❌ Trial health check FAILED | {task_id}")
                logger.warning(f"🧪 Container logs:\n{logs[:500]}")
                return {
                    "passed": False,
                    "error": f"App did not respond on port {port} within 45 seconds",
                    "phase": "health",
                    "logs": logs[:2000],
                    "image_tag": image_tag,
                }

            # 3c: Smoke-test the main page content
            if main_resp is not None:
                body = main_resp.text
                content_type = main_resp.headers.get("content-type", "")

                # Check response has actual content
                if len(body.strip()) == 0:
                    smoke_errors.append("Main page returned empty body")

                # For HTML responses, check for basic structure
                if "text/html" in content_type:
                    if "<html" not in body.lower() and "<!doctype" not in body.lower():
                        smoke_errors.append("Main page HTML missing <html> or <!doctype> tag")

                # Check for common error pages served with 200
                error_indicators = [
                    "Internal Server Error",
                    "Traceback (most recent call last)",
                    "ModuleNotFoundError",
                    "ImportError",
                    "SyntaxError",
                    "NameError",
                ]
                for indicator in error_indicators:
                    if indicator in body:
                        smoke_errors.append(f"Main page contains error: '{indicator}'")
                        break

            # 3d: Test additional API/static endpoints
            # Check /favicon.ico or /static/ — common assets that should not 500
            for probe_path in ["/favicon.ico", "/static/"]:
                try:
                    probe_resp = await client.get(f"{base_url}{probe_path}")
                    if probe_resp.status_code >= 500:
                        smoke_errors.append(f"GET {probe_path} returned {probe_resp.status_code}")
                except Exception:
                    pass  # 404 or connection resets are fine

            # 3e: If app uses SocketIO, verify the handshake endpoint
            try:
                sio_resp = await client.get(
                    f"{base_url}/socket.io/",
                    params={"EIO": "4", "transport": "polling"},
                )
                if sio_resp.status_code >= 500:
                    smoke_errors.append(f"SocketIO endpoint returned {sio_resp.status_code}")
                elif sio_resp.status_code == 200:
                    logger.info("🧪 SocketIO handshake OK")
            except Exception:
                pass  # Not all apps use SocketIO — ignore connection errors

        # Collect container logs for diagnostics
        try:
            logs = container.logs(tail=50).decode("utf-8", errors="replace")
        except Exception:
            logs = ""

        # Check for error patterns in container logs
        log_error_patterns = [
            "Traceback (most recent call last)",
            "ModuleNotFoundError",
            "ImportError",
            "SyntaxError",
            "RuntimeError",
            "OSError: [Errno",
        ]
        for pattern in log_error_patterns:
            if pattern in logs:
                smoke_errors.append(f"Container logs contain: '{pattern}'")
                break

        if smoke_errors:
            error_summary = "; ".join(smoke_errors)
            logger.warning(f"🧪 ❌ Trial smoke test FAILED | {task_id} | {error_summary}")
            return {
                "passed": False,
                "error": f"App starts but smoke test failed: {error_summary}",
                "phase": "smoke",
                "logs": logs[:2000],
                "image_tag": image_tag,
            }

        logger.info(f"🧪 ✅ Trial smoke test PASSED | {task_id}")
        return {
            "passed": True,
            "image_tag": image_tag,
            "logs": logs[:1000],
        }

    except Exception as e:
        logger.error(f"🧪 TRIAL_ERROR | {task_id} | {type(e).__name__}: {e}")
        return {"passed": False, "error": str(e), "phase": "unknown", "logs": ""}

    finally:
        # Always clean up the trial container
        if container:
            try:
                container.remove(force=True)
                logger.info(f"🧪 Trial container cleaned up")
            except Exception:
                pass


@activity.defn
async def stop_deployment_container(deployment_id: str) -> Dict[str, Any]:
    """Stop a deployment container."""
    import docker
    import httpx
    
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    
    logger.info(f"⏹️  STOP_DEPLOYMENT | {deployment_id}")
    
    try:
        # Fetch deployment details
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{control_plane_url}/api/deployments/{deployment_id}")
            resp.raise_for_status()
            deployment = resp.json()
        
        container_id = deployment.get("container_id")
        if not container_id:
            raise Exception("No container_id — deployment not running?")
        
        docker_client = get_docker_client()
        
        try:
            container = docker_client.containers.get(container_id)
            container.stop(timeout=10)
            container.remove(force=True)
            logger.info(f"✅ DEPLOYMENT_STOPPED | {deployment_id} | Container: {container_id[:12]}")
        except docker.errors.NotFound:
            logger.warning(f"Container {container_id[:12]} already removed")
        
        # Update deployment record
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.patch(
                f"{control_plane_url}/api/deployments/{deployment_id}",
                json={
                    "status": "stopped",
                    "container_id": None,
                    "host_port": None,
                    "url": None,
                },
            )
        
        return {"status": "stopped"}
    
    except Exception as e:
        logger.error(f"❌ DEPLOYMENT_STOP_FAILED | {deployment_id} | {e}")
        return {"error": str(e)}


# =============================================================================
# DAG Activities — Task-Centric DAG Orchestration
# =============================================================================

@activity.defn
async def load_dag(dag_id: str) -> Dict[str, Any]:
    """Load the full DAG definition including nodes from the control plane."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{control_plane_url}/api/dags/{dag_id}")
        if resp.status_code != 200:
            raise ApplicationError(f"Failed to load DAG {dag_id}: {resp.status_code}")
        return resp.json()


@activity.defn
async def update_node_status(
    dag_id: str,
    node_id: str,
    status: str,
    output_data: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    container_id: Optional[str] = None,
) -> bool:
    """Update the status of a DAG node via the control plane."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    payload: Dict[str, Any] = {"status": status}
    if output_data is not None:
        payload["output_data"] = output_data
    if task_id is not None:
        payload["task_id"] = task_id
    if container_id is not None:
        payload["container_id"] = container_id

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(
            f"{control_plane_url}/api/dags/{dag_id}/nodes/{node_id}",
            json=payload,
        )
        if resp.status_code not in (200, 204):
            logger.error(f"❌ update_node_status failed for {dag_id}/{node_id}: status={resp.status_code}, body={resp.text}")
        resp.raise_for_status()
        return True


@activity.defn
async def update_dag_status(dag_id: str, status: str) -> bool:
    """Update the overall DAG status via the control plane."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(
            f"{control_plane_url}/api/dags/{dag_id}",
            json={"status": status},
        )
        if resp.status_code not in (200, 204):
            logger.error(f"❌ update_dag_status failed for {dag_id}: status={resp.status_code}, body={resp.text}")
        resp.raise_for_status()
        return True


@activity.defn
async def post_dag_progress(dag_id: str, message: str) -> bool:
    """Post a progress message to the DAG's associated task (if any)."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Find any task associated with this DAG
        resp = await client.get(
            f"{control_plane_url}/api/tasks",
            params={"limit": 200},
        )
        if resp.status_code != 200:
            return False

        tasks = resp.json()
        for t in tasks:
            if t.get("dag_id") == dag_id:
                msg_resp = await client.post(
                    f"{control_plane_url}/api/tasks/{t['id']}/messages",
                    json={"content": message, "role": "system"},
                )
                return msg_resp.status_code in (200, 201)
    return False


@activity.defn
async def post_node_state_snapshot(dag_id: str, node_id: str, payload: Dict[str, Any]) -> bool:
    """Persist node execution-state snapshot for provenance and continuity."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/dags/{dag_id}/nodes/{node_id}/state-snapshots",
                json=payload,
            )
            return resp.status_code in (200, 201)
    except Exception as exc:
        logger.warning(f"⚠️ Failed to post node state snapshot for {dag_id}/{node_id}: {exc}")
        return False


@activity.defn
async def post_node_audit_event(dag_id: str, node_id: str, payload: Dict[str, Any]) -> bool:
    """Persist a structured audit event for DAG node execution."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/dags/{dag_id}/nodes/{node_id}/audit-events",
                json=payload,
            )
            return resp.status_code in (200, 201)
    except Exception as exc:
        logger.warning(f"⚠️ Failed to post node audit event for {dag_id}/{node_id}: {exc}")
        return False


@activity.defn
async def post_node_structured_output(dag_id: str, node_id: str, task_id: str, output: Dict[str, Any]) -> bool:
    """Persist structured node output with acceptance and skill compliance data."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    # Build structured payload from the collected output
    structured_payload = {
        "task_id": task_id,
        "status": "completed" if output.get("completed") else "failed",
        "objective": output.get("node_objective", ""),
        "success_criteria": output.get("success_criteria", []),
        "acceptance_verdict": "pass" if output.get("gate_result", {}).get("valid") else "fail",
        "acceptance_score": output.get("gate_result", {}).get("external_assessment", {}).get("score", 0),
        "criteria_met": {c: True for c in output.get("success_criteria", [])} if output.get("gate_result", {}).get("valid") else {},
        "skill_id": output.get("skill_id"),
        "skill_followed": None,  # Will be populated by skill reference extraction
        "deliverables_count": len(output.get("deliverables") or {}),
        "deliverables_keys": list((output.get("deliverables") or {}).keys()),
        "acquisition_log": output.get("acquisition_log", []),
        "llm_interaction_count": output.get("llm_interaction_count", 0),
        "output_text": output.get("output", ""),
        "error_text": output.get("error"),
        "workspace_step_path": output.get("workspace_step_path"),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/dags/{dag_id}/nodes/{node_id}/output",
                json=structured_payload,
            )
            return resp.status_code in (200, 201)
    except Exception as exc:
        logger.warning(f"⚠️ Failed to post structured output for {dag_id}/{node_id}: {exc}")
        return False


@activity.defn
async def create_node_task(
    dag_id: str,
    node_id: str,
    description: str,
    agent_image: str = "localhost:5000/openclaw-agent:openclaw",
    llm_model: str = "gemma3:4b",
    workspace_id: str = "",
) -> str:
    """Create a Task record for a DAG node and return the task_id."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    task_id = f"task-{uuid.uuid4().hex[:8]}"

    # Control-plane /api/tasks expects a base_image key (e.g. "browser", "openclaw"), not a full image URL.
    base_image = (agent_image or "openclaw").strip()
    if "/" in base_image and ":" in base_image:
        base_image = base_image.rsplit(":", 1)[-1]
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{control_plane_url}/api/tasks",
            json={
                "name": f"DAG node {node_id}",
                "description": description or f"Execute node {node_id}",
                "base_image": base_image,
                "llm_model": llm_model,
                "workspace_id": workspace_id or None,
                "dag_id": dag_id,
                "node_id": node_id,
                "auto_start": False,
            },
        )
        if resp.status_code not in (200, 201):
            raise ApplicationError(f"Failed to create task for node {node_id}: {resp.status_code} {resp.text}")
        data = resp.json()
        return data.get("id", task_id)


def _extract_acquisition_log(agent_logs: str) -> List[Dict[str, Any]]:
    """Extract a compact, structured acquisition trace from adapter logs."""
    entries: List[Dict[str, Any]] = []
    if not agent_logs:
        return entries

    lines = agent_logs.splitlines()
    current_tool: Dict[str, Any] | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("🧰 Tool:"):
            tool_call = line.split("🧰 Tool:", 1)[1].strip()
            tool_name = tool_call.split("(", 1)[0].strip() if "(" in tool_call else tool_call
            current_tool = {
                "kind": "tool_call",
                "tool": tool_name,
                "invocation": tool_call[:500],
            }
            entries.append(current_tool)
            continue

        if line.startswith("📤 Result:"):
            result_preview = line.split("📤 Result:", 1)[1].strip()
            if current_tool is not None:
                current_tool["result_preview"] = result_preview[:500]
            else:
                entries.append({
                    "kind": "result",
                    "result_preview": result_preview[:500],
                })
            continue

        if "HTTP/" in line:
            entries.append({
                "kind": "http_response",
                "result_preview": line[:500],
            })
            continue

        if line.startswith("✅ Written "):
            entries.append({
                "kind": "file_write",
                "result_preview": line[:500],
            })
            continue

        if "saved to /workspace/" in line.lower() or "written to /workspace/" in line.lower():
            entries.append({
                "kind": "artifact_saved",
                "result_preview": line[:500],
            })

    return entries


def _extract_task_output_text(task_output: Dict[str, Any]) -> str:
    """Extract the agent's actual response text from a stored task output."""
    import json as _json

    _decoder = _json.JSONDecoder()
    for source in (task_output.get("output"), (task_output.get("raw_result") or {}).get("output")):
        if not source or not isinstance(source, str):
            continue
        stripped = source.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                parsed, _end = _decoder.raw_decode(stripped)
                if isinstance(parsed, dict):
                    texts = [
                        p.get("text", "")
                        for p in parsed.get("payloads", [])
                        if isinstance(p, dict) and p.get("text")
                    ]
                    if texts:
                        return "\n".join(texts).strip()[:4000]
            except (_json.JSONDecodeError, TypeError):
                pass
        if stripped.lower() not in {"task completed successfully", "task failed", "task cancelled"}:
            return stripped[:4000]

    preview = (task_output.get("llm_response_preview") or "").strip()
    if preview and preview.lower() not in {"task completed successfully", "task failed", "task cancelled"}:
        return preview[:1200]

    return (task_output.get("agent_logs") or "")[:1200]


def _build_compact_step_summary(output: Dict[str, Any]) -> str:
    """Build a compact, bounded summary for UI/gating use."""
    parts: List[str] = []
    if output.get("status"):
        parts.append(f"status={output.get('status')}")
    if output.get("completed") is not None:
        parts.append(f"completed={output.get('completed')}")
    if output.get("deliverables") and isinstance(output.get("deliverables"), dict):
        keys = list((output.get("deliverables") or {}).keys())[:8]
        if keys:
            parts.append("deliverables=" + ", ".join(keys))
    if output.get("error"):
        parts.append("error=" + str(output.get("error"))[:220])
    output_text = str(output.get("output") or "").strip()
    if output_text:
        parts.append("result=" + output_text[:500].replace("\n", " "))
    return " | ".join(parts)[:1200]


def _build_stage_handoff(input_data: Dict[str, Any]) -> str:
    """Build a bounded 'stage handoff' block describing each predecessor node:
    what was done (tool/action trace), what was produced (deliverables + paths),
    and the outcome. This gives downstream nodes real context instead of only a
    bare output-text preview, so they know what upstream already accomplished and
    what new work remains.
    """
    if not input_data:
        return ""

    blocks: List[str] = ["--- Handoff from previous stages (what was done + produced) ---"]
    for src_node, data in input_data.items():
        if not isinstance(data, dict):
            blocks.append(f"[{src_node}] literal input: {str(data)[:500]}")
            continue

        # Status / outcome
        status = data.get("status") or "unknown"
        completed = data.get("completed")
        error = str(data.get("error") or "").strip()
        gate_failure = str(data.get("gate_failure") or "").strip()
        outcome = []
        if completed is not None:
            outcome.append(f"completed={completed}")
        if error:
            outcome.append(f"error={error[:200]}")
        if gate_failure:
            outcome.append(f"gate_failure={gate_failure[:200]}")
        outcome_str = "; ".join(outcome) if outcome else "ok"

        # What was done — structured tool/action trace
        acq = (data.get("acquisition_log") or [])
        if isinstance(acq, list) and acq:
            action_lines = []
            for entry in acq[:20]:
                if isinstance(entry, dict):
                    tool = entry.get("tool") or entry.get("kind") or "?"
                    inv = str(entry.get("invocation") or "")[:200]
                    res = str(entry.get("result_preview") or "")[:160]
                    if tool:
                        action_lines.append(f"    - {tool}: {inv}" + (f" -> {res}" if res else ""))
            what_done = "\n".join(action_lines) if action_lines else "(no tool trace recorded)"
        else:
            logs = str(data.get("agent_logs") or "")
            what_done = ("    " + "\n    ".join(logs.splitlines()[:15])) if logs else "(no logs)"

        # What was produced — deliverables + explicit paths
        dl = data.get("deliverables")
        produced = []
        if isinstance(dl, dict) and dl:
            for name, content in dl.items():
                ctype = "script" if name.lower().endswith((".py", ".js", ".sh")) else "data"
                size = len(str(content)) if content is not None else 0
                # Per-node output dirs: upstream files live under the source node's dir.
                produced.append(f"    - {name}  [{ctype}, {size} bytes]  at /workspace/{src_node}/{name}")
        produced_str = "\n".join(produced) if produced else "    (no deliverables)"

        # Primary output text
        output_text = _extract_task_output_text(data)
        output_preview = output_text[:1500] + ("..." if len(output_text) > 1500 else "") if output_text else ""

        block = (
            f"[{src_node}] status={status}; {outcome_str}\n"
            f"  WHAT WAS DONE:\n{what_done}\n"
            f"  PRODUCED:\n{produced_str}"
        )
        if output_preview:
            block += f"\n  OUTPUT: {output_preview}"
        blocks.append(block)

    return "\n\n".join(blocks)


async def _assess_objective_alignment_external(
    task_id: str,
    output: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assess step outcome against objective using external LLM router."""
    import httpx
    import json as _json

    cfg = config or {}
    assess_cfg = cfg.get("objective_assessment") or {}
    if assess_cfg.get("enabled") is False:
        return {"enabled": False, "verdict": "skip", "reason": "disabled by node config"}

    objective_text = (
        cfg.get("node_objective")
        or cfg.get("objective")
        or cfg.get("description")
        or ""
    )
    objective_text = str(objective_text).strip()
    if not objective_text:
        return {"enabled": True, "verdict": "skip", "reason": "no objective text"}

    model = assess_cfg.get("model") or "gemini-flash-lite-latest"
    compact_summary = _build_compact_step_summary(output)
    deliverable_keys = list(((output.get("deliverables") or {}) if isinstance(output.get("deliverables"), dict) else {}).keys())[:20]
    acquisition_log = (output.get("acquisition_log") or [])[:20]

    prompt = (
        "You are a strict workflow step assessor. Compare ACTUAL RESULT to STEP OBJECTIVE. "
        "Return JSON only with keys: verdict, score, summary, missing_requirements, next_actions, evidence_quality.\n"
        "verdict must be 'pass' or 'fail'. score must be integer 0-100.\n\n"
        f"STEP OBJECTIVE:\n{objective_text[:2400]}\n\n"
        f"RESULT SUMMARY:\n{compact_summary}\n\n"
        f"DELIVERABLE KEYS:\n{deliverable_keys}\n\n"
        f"ACQUISITION LOG (truncated):\n{_json.dumps(acquisition_log)[:2400]}\n"
    )

    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    req = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{cp_url}/api/llm/v1/chat/completions", json=req)
            resp.raise_for_status()
            data = resp.json()
            content = (
                (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                or "{}"
            )
            text = str(content).strip()
            if "```" in text:
                text = text.replace("```json", "").replace("```", "").strip()
            start = text.find("{")
            end = text.rfind("}")
            parsed = _json.loads(text[start:end + 1] if start != -1 and end != -1 else text)
            verdict = str(parsed.get("verdict", "fail")).lower()
            score = int(parsed.get("score", 0) or 0)
            return {
                "enabled": True,
                "verdict": "pass" if verdict == "pass" else "fail",
                "score": max(0, min(score, 100)),
                "summary": str(parsed.get("summary", ""))[:400],
                "missing_requirements": parsed.get("missing_requirements") or [],
                "next_actions": parsed.get("next_actions") or [],
                "evidence_quality": str(parsed.get("evidence_quality", ""))[:120],
                "model": model,
            }
    except Exception as exc:
        return {
            "enabled": True,
            "verdict": "error",
            "score": 0,
            "summary": f"assessment_error: {str(exc)[:220]}",
            "missing_requirements": [],
            "next_actions": [],
            "evidence_quality": "unknown",
            "model": model,
        }


def _bounded_payload(obj: Any, max_str: int = 40000, max_total: int = 700000) -> Any:
    """Recursively trim a payload to stay under Temporal's 2 MB payload limit.

    Temporal rejects activity inputs/results larger than ~2 MB
    ("ScheduleActivityTaskCommandAttributes.Input exceeds size limit"). Node
    outputs can contain large agent logs and deliverable file contents; keep
    structure (keys, summaries) but cap string sizes and the overall budget.
    """
    remaining = [max_total]

    def _trim(v):
        if isinstance(v, str):
            if len(v) > max_str:
                v = v[:max_str] + f"\n...[truncated {len(v)} chars]"
            if len(v) > remaining[0]:
                v = v[:max(1, remaining[0])]
            remaining[0] -= len(v)
            return v
        if isinstance(v, dict):
            out: Dict[str, Any] = {}
            for k, val in v.items():
                if remaining[0] <= 0:
                    break
                out[k] = _trim(val)
            return out
        if isinstance(v, list):
            out = []
            for val in v:
                if remaining[0] <= 0:
                    break
                out.append(_trim(val))
            return out
        return v

    return _trim(obj)


@activity.defn
async def collect_node_output(task_id: str) -> Dict[str, Any]:
    """Collect output from a completed node task."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
        if resp.status_code != 200:
            return {"error": f"Failed to fetch task {task_id}"}

        task = resp.json()
        node_id = task.get("node_id") or ""

        # Fetch the latest output
        outputs_resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}/outputs")
        outputs_payload = outputs_resp.json() if outputs_resp.status_code == 200 else {}
        outputs = []
        if isinstance(outputs_payload, dict):
            outputs = outputs_payload.get("outputs", []) or []
        elif isinstance(outputs_payload, list):
            outputs = outputs_payload

        result = {
            "task_id": task_id,
            "node_id": node_id,
            "status": task.get("status"),
        }

        if outputs:
            latest = outputs[-1]
            result["output"] = _extract_task_output_text(latest)
            result["agent_logs"] = latest.get("agent_logs", "")
            result["completed"] = latest.get("completed")
            result["error"] = latest.get("error")
            result["deliverables"] = latest.get("deliverables")
            result["deliverables_keys"] = latest.get("deliverables_keys")
            # Add output_path for downstream input mappings (e.g., "fetch-investor-pdf.output_path")
            deliverables = latest.get("deliverables") or {}
            if deliverables:
                # Prefer non-script PDF files (actual data outputs like latest_report.pdf over fetch_report.py)
                pdf_files = [k for k in deliverables.keys() if k.lower().endswith('.pdf')]
                # Filter out Python script files that happen to be named *.pdf.py or similar
                data_pdfs = [k for k in pdf_files if not k.endswith('.py')]
                if data_pdfs:
                    result["output_path"] = data_pdfs[0]
                elif pdf_files:
                    result["output_path"] = pdf_files[0]
                else:
                    # Prefer non-Python files as primary output
                    non_py = [k for k in deliverables.keys() if not k.endswith('.py')]
                    if non_py:
                        result["output_path"] = sorted(non_py)[0]
                    else:
                        result["output_path"] = sorted(deliverables.keys())[0]
            # Don't include full raw_result - it can be huge (base64 PDFs, full LLM responses)
            # Useful fields are already extracted above
            raw = latest.get("raw_result") or {}
            result["parse_error"] = bool(raw.get("parse_error"))
            result["iteration"] = latest.get("iteration")
            result["workspace_step_path"] = raw.get("workspace_step_path")
            result["acquisition_log"] = _extract_acquisition_log(result["agent_logs"])
            result["compact_summary"] = _build_compact_step_summary(result)

        return _bounded_payload(result)


def _summarize_upstream_state(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build compact predecessor-state summaries for downstream review."""
    summaries: List[Dict[str, Any]] = []

    for src_node, data in input_data.items():
        if not isinstance(data, dict):
            summaries.append({
                "node_id": src_node,
                "kind": "literal_input",
                "value": str(data)[:500],
            })
            continue

        deliverables = data.get("deliverables")
        acquisition_log = data.get("acquisition_log") or []
        acquisition_tools = []
        output_text = _extract_task_output_text(data)
        if isinstance(acquisition_log, list):
            acquisition_tools = [
                entry.get("tool")
                for entry in acquisition_log
                if isinstance(entry, dict) and entry.get("tool")
            ][:10]

        summary = {
            "node_id": src_node,
            "status": data.get("status"),
            "completed": data.get("completed"),
            "error": str(data.get("error") or "")[:300],
            "gate_failure": str(data.get("gate_failure") or "")[:300],
            "deliverable_keys": sorted(deliverables.keys())[:20] if isinstance(deliverables, dict) else [],
            "acquisition_tools": acquisition_tools,
            "output_preview": output_text[:1200],
            "log_preview": str(data.get("agent_logs") or "")[:800],
        }
        summaries.append(summary)

    return summaries


def _build_prior_state_review_prompt(upstream_state_review: List[Dict[str, Any]]) -> str:
    """Render the mandatory predecessor-state review block for downstream nodes."""
    if not upstream_state_review:
        return ""

    lines = [
        "Before you do any new work, review the predecessor state below and use it to guide this step.",
        "Your first output section must be 'Previous Step Review' and it must state:",
        "- which predecessor states you reviewed",
        "- what outputs or failures from those states matter for this step",
        "- how those reviewed states change your plan for the current step",
        "If predecessor state is incomplete, contradictory, or failed, call that out explicitly before continuing.",
        "",
        "Predecessor state summaries:",
    ]

    for summary in upstream_state_review:
        node_id = summary.get("node_id", "unknown")
        lines.append(f"- Node {node_id}")
        if summary.get("kind") == "literal_input":
            lines.append(f"  literal input: {summary.get('value', '')}")
            continue
        lines.append(f"  status: {summary.get('status')}")
        if summary.get("completed") is not None:
            lines.append(f"  completed: {summary.get('completed')}")
        if summary.get("gate_failure"):
            lines.append(f"  gate failure: {summary.get('gate_failure')}")
        if summary.get("error"):
            lines.append(f"  error: {summary.get('error')}")
        if summary.get("deliverable_keys"):
            lines.append(f"  deliverable keys: {', '.join(summary.get('deliverable_keys', []))}")
        if summary.get("acquisition_tools"):
            lines.append(f"  acquisition tools: {', '.join(summary.get('acquisition_tools', []))}")
        if summary.get("log_preview"):
            lines.append(f"  log preview: {summary.get('log_preview')}")

    return "\n".join(lines)


@activity.defn
async def evaluate_node_gate(task_id: str, output: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate node output quality gate.

    The gate defaults to strict mode:
    - task status must be completed
    - latest output.completed must be true
    - error must be empty
    - deliverables must be non-empty

    Node config can opt out of deliverables requirement using:
    config.deliverable_gate.require_deliverables = false
    config.deliverable_gate.enabled = false
    """

    def _is_trueish(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in ("1", "true", "yes", "y")

    gate_cfg = ((config or {}).get("deliverable_gate") or {}) if isinstance(config, dict) else {}
    if gate_cfg.get("enabled") is False:
        return {"valid": True, "reason": "deliverable gate disabled by node config"}

    if str(output.get("status", "")).lower() != "completed":
        return {"valid": False, "reason": f"task status is '{output.get('status')}', expected 'completed'"}

    if not _is_trueish(output.get("completed")):
        return {"valid": False, "reason": "latest task output is not marked completed=true"}

    if output.get("error"):
        return {"valid": False, "reason": f"task output error: {str(output.get('error'))[:200]}"}

    if output.get("parse_error"):
        return {"valid": False, "reason": "task output parse_error is true"}

    require_deliverables = gate_cfg.get("require_deliverables", True)
    if require_deliverables:
        deliverables = output.get("deliverables")
        if not isinstance(deliverables, dict) or len(deliverables) == 0:
            return {"valid": False, "reason": "task output has no deliverables"}

    # Source-evidence check: detect hallucination/mock patterns in agent logs.
    # If the node config declares require_real_sources=true (default for fetch/browse nodes)
    # the logs must not contain mock/fabricated data signals without any real HTTP/file fetch.
    require_real_sources = gate_cfg.get("require_real_sources", False)
    if require_real_sources:
        logs = str(output.get("agent_logs") or "")
        _mock_signals = [
            "mock_data", "mock data", "mocking the respo", "placeholder",
            "# mocking", "# mock", "Mocking the", "MockData",
            "fake_data", "simulated_data", "dummy_data",
        ]
        _real_signals = [
            "HTTP/", "http_status", "curl", "requests.get", "httpx",
            "urllib", "fetch(", "wget ", "lightpanda fetch",
            "200", "\"status_code\"", "status_code =",
        ]
        mock_hit = any(s.lower() in logs.lower() for s in _mock_signals)
        real_hit = any(s in logs for s in _real_signals)
        if mock_hit and not real_hit:
            return {
                "valid": False,
                "reason": "hallucination_risk: logs contain mock/placeholder patterns with no evidence of real network fetch",
            }

    assessment = await _assess_objective_alignment_external(task_id, output, config)
    if assessment.get("enabled") and assessment.get("verdict") == "fail":
        missing = assessment.get("missing_requirements") or []
        missing_text = ", ".join(str(x) for x in missing[:3])
        reason = "objective_alignment_failed"
        if missing_text:
            reason = f"{reason}: missing {missing_text}"
        return {
            "valid": False,
            "reason": reason,
            "external_assessment": assessment,
        }
    if assessment.get("enabled") and assessment.get("verdict") == "error":
        return {
            "valid": False,
            "reason": "objective_assessment_error",
            "external_assessment": assessment,
        }
    return {"valid": True, "reason": "ok", "external_assessment": assessment}


@activity.defn
async def evaluate_edge_condition(
    dag_json: Dict[str, Any],
    from_node: str,
    to_node: str,
    node_outputs: Dict[str, Any],
) -> bool:
    """Evaluate whether a conditional edge should be followed.

    Checks the edge condition against the source node's output.
    If no condition is specified, the edge is always followed.
    """
    edges = dag_json.get("edges", [])
    for edge in edges:
        if edge.get("from") == from_node and edge.get("to") == to_node:
            condition = edge.get("condition")
            if not condition:
                return True

            # Simple condition evaluation:
            # "on_failure" → follow if source node failed
            # "on_success" → follow if source node succeeded
            # "on_output_contains:<text>" → follow if output contains text
            source_output = node_outputs.get(from_node, {})
            source_status = source_output.get("status", "")

            if condition == "on_failure":
                return source_status in ("failed", "error")
            elif condition == "on_success":
                return source_status in ("completed", "success")
            elif condition.startswith("on_output_contains:"):
                search_text = condition.split(":", 1)[1]
                logs = source_output.get("agent_logs", "")
                return search_text.lower() in logs.lower()
            else:
                logger.warning(f"Unknown edge condition: {condition}")
                return True
    return True


@activity.defn
async def finalize_dag(dag_id: str, status: str) -> bool:
    """Finalize a DAG — update status and timestamp."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(
            f"{control_plane_url}/api/dags/{dag_id}",
            json={
                "status": status,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if resp.status_code not in (200, 204):
            logger.error(f"❌ finalize_dag failed for {dag_id}: status={resp.status_code}, body={resp.text}")
        resp.raise_for_status()
        return True


@activity.defn
async def persist_task_workflow_id(task_id: str, workflow_id: str) -> bool:
    """Persist a task's Temporal workflow ID in control-plane.

    Capability approvals are routed using task.workflow_id, so DAG child
    workflows must publish their IDs here.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{control_plane_url}/api/tasks/{task_id}",
                json={"workflow_id": workflow_id},
            )
            return resp.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"Could not persist workflow_id for {task_id}: {e}")
        return False


# =============================================================================
# DAG Workflows
# =============================================================================

@workflow.defn
class DAGNodeWorkflow:
    """Execute a single DAG node.

    Creates a Task record, then delegates execution to AgentTaskWorkflow
    as a child workflow, reusing all existing agent infrastructure
    (container launch, LLM polling, capability approval, deployment).
    """

    @workflow.run
    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        dag_id = params["dag_id"]
        node_id = params["node_id"]
        description = params.get("description", "")
        config = dict(params.get("config", {}) or {})
        input_data = params.get("input_data", {})
        state_context = params.get("state_context", {})
        workspace_id = params.get("workspace_id", "")

        # Prefer the DAG-inherited enriched image over the static base_image
        # from the node config.  dag_image is injected by DAGWorkflow when a
        # previous wave already built capabilities.
        agent_image = config.get("dag_image") or config.get("base_image", "openclaw")
        if "/" not in agent_image:
            agent_image = f"localhost:5000/openclaw-agent:{agent_image}"
        if config.get("dag_image"):
            logger.info(f"🔗 DAGNodeWorkflow | Node {node_id} inheriting DAG image: {agent_image}")
        llm_model = config.get("llm_model", "gemini-flash-lite-latest")

        logger.info(f"🔧 DAGNodeWorkflow | DAG: {dag_id} | Node: {node_id} | model: {llm_model}")

        # Initialize task_id to None so exception handler can always reference it safely
        task_id: Optional[str] = None
        upstream_state_review = _summarize_upstream_state(input_data)

        # Update node to RUNNING
        await workflow.execute_activity(
            update_node_status,
            args=[dag_id, node_id, "running"],
            start_to_close_timeout=timedelta(seconds=15),
        )

        # Post start message indicating base image and skill consumed
        base_img = config.get("base_image", "openclaw")
        dag_img = config.get("dag_image")
        if dag_img:
            dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
            img_info = f"{base_img} (built: {dag_img_tag})"
        else:
            img_info = base_img
        skill_info = config.get("selected_skill_v2_id") or config.get("skill_id") or "inline/custom"
        
        await workflow.execute_activity(
            post_dag_progress,
            args=[dag_id, f"🔨 Node '{node_id}' started execution (image: '{img_info}', skill: '{skill_info}')"],
            start_to_close_timeout=timedelta(seconds=15),
        )

        # Build explicit objective contract so each step has clear success target.
        node_objective = str(config.get("node_objective") or description or "").strip()
        success_criteria = config.get("success_criteria")
        if not isinstance(success_criteria, list):
            success_criteria = []
        config["node_objective"] = node_objective

        # Build the follow-up prompt from objective + input_data
        follow_up_parts = []
        # Deliverables-dir instruction FIRST so it is never truncated away by the
        # FOLLOW_UP env limit — agents must know exactly where to write outputs.
        follow_up_parts.append(
            f"--- Deliverables directory ---\n"
            f"This step's deliverables directory is `/workspace/{node_id}/` — create it if needed. "
            f"Write your FINAL deliverables (reports, parsed data files, code artifacts) there; only files in "
            f"that directory are collected. Put intermediate/raw products (fetched HTML pages, caches, scratch) "
            f"in `/tmp` or `/workspace/.cache/` — they are NOT collected. If the task fetches web data, save the "
            f"raw pages outside the deliverables directory and place only the parsed/structured data inside it."
        )
        if node_objective:
            follow_up_parts.append("--- Step Objective ---")
            follow_up_parts.append(node_objective)
        if success_criteria:
            follow_up_parts.append("--- Success Criteria ---")
            for criterion in success_criteria[:8]:
                follow_up_parts.append(f"- {str(criterion)[:240]}")
        template_guidance = config.get("template_guidance")
        if template_guidance:
            follow_up_parts.append(
                "\n--- FOLLOW THE GUIDANCE (re-running a proven routine) ---\n"
                "In a previous successful run, THIS step did:\n"
                f"{str(template_guidance)[:1500]}\n"
                "Follow the same approach now with the current objective and inputs. "
                "When you act, briefly note what you are repeating from the previous run vs. adapting."
            )
        if input_data:
            handoff = _build_stage_handoff(input_data)
            if handoff:
                follow_up_parts.append("\n" + handoff)
                follow_up_parts.append(
                    "\nThe files listed above are INPUTS from previous stages — treat them as read-only. "
                    "Do NOT regenerate or re-run upstream scripts. Your job is to produce NEW deliverables "
                    "specific to this step's objective below, using the upstream outputs as inputs where relevant."
                )
        if upstream_state_review:
            follow_up_parts.append("\n--- Required review of previous step state ---")
            follow_up_parts.append(_build_prior_state_review_prompt(upstream_state_review))
        if state_context:
            follow_up_parts.append("\n--- Execution state context ---")
            follow_up_parts.append(str(state_context)[:4000])
        follow_up = "\n".join(follow_up_parts)

        try:
            # Create a task for tracking
            task_id = await workflow.execute_activity(
                create_node_task,
                args=[dag_id, node_id, description, agent_image, llm_model, workspace_id],
                start_to_close_timeout=timedelta(seconds=30),
            )

            # Update node with task_id
            await workflow.execute_activity(
                update_node_status,
                args=[dag_id, node_id, "running", None, task_id],
                start_to_close_timeout=timedelta(seconds=15),
            )

            await workflow.execute_activity(
                post_node_state_snapshot,
                args=[
                    dag_id,
                    node_id,
                    {
                        "task_id": task_id,
                        "phase": "running",
                        "status": "running",
                        "input_context": {
                            "input_data": input_data,
                            "state_context": state_context,
                            "upstream_state_review": upstream_state_review,
                        },
                        "completion_state": {
                            "description": "Node task started",
                        },
                    },
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )

            # Delegate to AgentTaskWorkflow — this handles iterations,
            # capability approval, deployment requests, and finalization.
            child_workflow_id = f"agent-task-{dag_id}-{node_id}"

            await workflow.execute_activity(
                persist_task_workflow_id,
                args=[task_id, child_workflow_id],
                start_to_close_timeout=timedelta(seconds=10),
            )

            result = await workflow.execute_child_workflow(
                AgentTaskWorkflow.run,
                args=[task_id, llm_model, agent_image, follow_up, dag_id],
                id=child_workflow_id,
            )

            # Collect full output
            output = await workflow.execute_activity(
                collect_node_output,
                args=[task_id],
                start_to_close_timeout=timedelta(seconds=30),
            )

            gate_result = await workflow.execute_activity(
                evaluate_node_gate,
                args=[task_id, output, config],
                start_to_close_timeout=timedelta(seconds=30),
            )

            # Soft-fail retry policy: when objective assessment fails,
            # run one corrective retry before final failure.
            should_retry_assessment = (
                not gate_result.get("valid", False)
                and str(gate_result.get("reason", "")).startswith("objective_alignment_failed")
                and ((config.get("objective_assessment") or {}).get("retry_on_fail", True))
            )
            if should_retry_assessment:
                external = gate_result.get("external_assessment") or {}
                corrective_actions = external.get("next_actions") or []
                missing_requirements = external.get("missing_requirements") or []
                assessment_summary = str(external.get("summary") or "").strip()
                assessment_score = external.get("score")
                retry_lines = [
                    "",
                    "--- External Step Assessment (Retry Required) ---",
                    "Your previous output did not satisfy the step objective.",
                    "You must close ALL gaps below before finishing this retry.",
                ]
                if assessment_score is not None:
                    retry_lines.append(f"Assessment score: {assessment_score}/100")
                if assessment_summary:
                    retry_lines.append(f"Assessment summary: {assessment_summary[:500]}")
                if missing_requirements:
                    retry_lines.append("Missing required deliverables:")
                    for item in missing_requirements[:20]:
                        retry_lines.append(f"- {str(item)[:280]}")
                retry_lines.append("Corrective actions to apply:")
                for action in corrective_actions[:8]:
                    retry_lines.append(f"- {str(action)[:280]}")
                retry_lines.extend(
                    [
                        "Before you finish, include an explicit 'Acceptance Criteria Check' section",
                        "listing each missing requirement and where it was produced.",
                    ]
                )
                retry_follow_up = follow_up + "\n" + "\n".join(retry_lines)

                await workflow.execute_activity(
                    post_node_audit_event,
                    args=[
                        dag_id,
                        node_id,
                        {
                            "task_id": task_id,
                            "event_type": "objective_assessment_retry",
                            "severity": "warning",
                            "message": "Objective assessment failed; running one corrective retry",
                            "event_data": {
                                "assessment": external,
                                "task_id": task_id,
                            },
                        },
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                retry_result = await workflow.execute_child_workflow(
                    AgentTaskWorkflow.run,
                    args=[task_id, llm_model, result.get("current_image", agent_image), retry_follow_up, dag_id],
                    id=f"{child_workflow_id}-assessment-retry",
                )

                output = await workflow.execute_activity(
                    collect_node_output,
                    args=[task_id],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                gate_result = await workflow.execute_activity(
                    evaluate_node_gate,
                    args=[task_id, output, config],
                    start_to_close_timeout=timedelta(seconds=30),
                )

                if retry_result.get("current_image"):
                    result["current_image"] = retry_result.get("current_image")

            if not gate_result.get("valid", False):
                reason = gate_result.get("reason", "deliverable gate failed")
                output["gate_failure"] = reason
                logger.error(f"🛑 NODE_GATE_FAILED | DAG: {dag_id} | Node: {node_id} | Reason: {reason}")
                await workflow.execute_activity(
                    update_node_status,
                    args=[dag_id, node_id, "failed", output],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                base_img = config.get("base_image", "openclaw")
                dag_img = config.get("dag_image")
                if dag_img:
                    dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                    img_info = f"{base_img} (built: {dag_img_tag})"
                else:
                    img_info = base_img
                skill_info = config.get("selected_skill_v2_id") or config.get("skill_id") or "inline/custom"
                await workflow.execute_activity(
                    post_dag_progress,
                    args=[dag_id, f"❌ Node '{node_id}' finished with status 'failed' (gate failure: {reason}) (image: '{img_info}', skill: '{skill_info}')"],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                await workflow.execute_activity(
                    post_node_state_snapshot,
                    args=[
                        dag_id,
                        node_id,
                        {
                            "task_id": task_id,
                            "phase": "gate_failed",
                            "status": "failed",
                            "input_context": {
                                "input_data": input_data,
                                "state_context": state_context,
                                "upstream_state_review": upstream_state_review,
                            },
                            "output_context": output,
                            "completion_state": {
                                "final_status": "failed",
                                "reason": reason,
                            },
                            "acquisition_log": output.get("acquisition_log", []),
                            "acceptance_result": gate_result,
                            "pending_items": [{"type": "gate_failure", "reason": reason}],
                        },
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                await workflow.execute_activity(
                    post_node_audit_event,
                    args=[
                        dag_id,
                        node_id,
                        {
                            "task_id": task_id,
                            "event_type": "acceptance_failed",
                            "severity": "critical",
                            "message": f"Node failed acceptance gate: {reason}",
                            "event_data": {
                                "gate_result": gate_result,
                                "task_id": task_id,
                            },
                        },
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                return {
                    "node_id": node_id,
                    "status": "failed",
                    "output": output,
                    "gate_failure": reason,
                    "current_image": result.get("current_image", ""),
                }

            # Determine final status from AgentTaskWorkflow result
            node_status = "completed" if result.get("status") != "failed" else "failed"

            # Capture the (possibly enriched) image from the agent workflow
            node_current_image = result.get("current_image", "")

            # Update node as completed/failed
            await workflow.execute_activity(
                update_node_status,
                args=[dag_id, node_id, node_status, output],
                start_to_close_timeout=timedelta(seconds=15),
            )

            base_img = config.get("base_image", "openclaw")
            dag_img = config.get("dag_image") or node_current_image
            if dag_img:
                dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                img_info = f"{base_img} (built: {dag_img_tag})"
            else:
                img_info = base_img
            skill_info = config.get("selected_skill_v2_id") or config.get("skill_id") or "inline/custom"
            status_emoji = "✅" if node_status == "completed" else "❌"
            await workflow.execute_activity(
                post_dag_progress,
                args=[dag_id, f"{status_emoji} Node '{node_id}' finished with status '{node_status}' (image: '{img_info}', skill: '{skill_info}')"],
                start_to_close_timeout=timedelta(seconds=15),
            )

            await workflow.execute_activity(
                post_node_state_snapshot,
                args=[
                    dag_id,
                    node_id,
                    {
                        "task_id": task_id,
                        "phase": "completed" if node_status == "completed" else "failed",
                        "status": node_status,
                        "input_context": {
                            "input_data": input_data,
                            "state_context": state_context,
                            "upstream_state_review": upstream_state_review,
                        },
                        "output_context": output,
                        "completion_state": {
                            "final_status": node_status,
                            "current_image": node_current_image,
                        },
                        "acquisition_log": output.get("acquisition_log", []),
                        "acceptance_result": gate_result,
                        "acceptance_state": {
                            "verdict": "pass" if gate_result.get("valid") else "fail",
                            "score": gate_result.get("external_assessment", {}).get("score", 0),
                            "criteria_results": [],
                            "checked_at": workflow.now().isoformat(),
                        },
                    },
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )

            # Post structured output for testability
            await workflow.execute_activity(
                post_node_structured_output,
                args=[dag_id, node_id, task_id, {
                    "gate_result": gate_result,
                    "node_objective": node_objective,
                    "success_criteria": success_criteria,
                    "skill_id": config.get("selected_skill_v2_id") or config.get("skill_id"),
                }],
                start_to_close_timeout=timedelta(seconds=15),
            )

            return {
                "node_id": node_id,
                "status": node_status,
                "output": output,
                "current_image": node_current_image,
            }

        except Exception as e:
            logger.error(f"❌ DAGNodeWorkflow failed | Node: {node_id} | Error: {e}")
            failure_output = {"error": str(e), "status": "failed"}
            await workflow.execute_activity(
                update_node_status,
                args=[dag_id, node_id, "failed", failure_output],
                start_to_close_timeout=timedelta(seconds=15),
            )
            base_img = config.get("base_image", "openclaw")
            dag_img = config.get("dag_image")
            if dag_img:
                dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                img_info = f"{base_img} (built: {dag_img_tag})"
            else:
                img_info = base_img
            skill_info = config.get("selected_skill_v2_id") or config.get("skill_id") or "inline/custom"
            await workflow.execute_activity(
                post_dag_progress,
                args=[dag_id, f"❌ Node '{node_id}' finished with status 'failed' (exception: {str(e)[:150]}) (image: '{img_info}', skill: '{skill_info}')"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            # Write terminal failed state snapshot so UI shows phase=failed with output
            await workflow.execute_activity(
                post_node_state_snapshot,
                args=[
                    dag_id,
                    node_id,
                    {
                        "task_id": task_id,
                        "phase": "failed",
                        "status": "failed",
                        "input_context": {
                            "input_data": input_data,
                            "state_context": state_context,
                            "upstream_state_review": upstream_state_review,
                        },
                        "output_context": failure_output,
                        "completion_state": {
                            "final_status": "failed",
                            "reason": str(e),
                        },
                        "acquisition_log": failure_output.get("acquisition_log", []),
                        "acceptance_result": {
                            "valid": False,
                            "reason": f"node_workflow_exception: {str(e)[:300]}",
                        },
                        "acceptance_state": {
                            "verdict": "fail",
                            "score": 0,
                            "criteria_results": [],
                            "checked_at": workflow.now().isoformat(),
                            "reason": str(e),
                        },
                        "pending_items": [{"type": "exception", "reason": str(e)[:300]}],
                    },
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            # Post structured output for failed node
            await workflow.execute_activity(
                post_node_structured_output,
                args=[dag_id, node_id, task_id, {
                    "gate_result": {"valid": False, "reason": str(e)},
                    "error_text": str(e),
                }],
                start_to_close_timeout=timedelta(seconds=15),
            )
            await workflow.execute_activity(
                post_node_audit_event,
                args=[
                    dag_id,
                    node_id,
                    {
                        "event_type": "node_workflow_exception",
                        "severity": "critical",
                        "message": str(e),
                        "event_data": {
                            "input_data": input_data,
                            "state_context": state_context,
                        },
                    },
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            return {
                "node_id": node_id,
                "status": "failed",
                "error": str(e),
            }


@workflow.defn
class DAGWorkflow:
    """Orchestrate all nodes in a Master DAG.

    Performs topological traversal: identifies ready nodes (all deps completed),
    launches them in parallel via child DAGNodeWorkflow instances, collects
    outputs, evaluates conditional edges, and repeats until all nodes finish.
    """

    @workflow.run
    async def run(self, dag_id: str) -> Dict[str, Any]:
        logger.info(f"🚀 DAGWorkflow started | DAG: {dag_id}")

        # Load DAG definition
        dag_data = await workflow.execute_activity(
            load_dag,
            args=[dag_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        dag_json = dag_data.get("dag_json", {})
        nodes_list = dag_data.get("nodes", [])
        dag_workspace_id = dag_data.get("workspace_id", "")

        if not nodes_list:
            logger.warning(f"⚠️ DAG {dag_id} has no nodes")
            await workflow.execute_activity(
                update_dag_status,
                args=[dag_id, "completed"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            return {"dag_id": dag_id, "status": "completed", "nodes_executed": 0}

        # Build node lookup and adjacency
        node_map: Dict[str, Dict[str, Any]] = {}
        for n in nodes_list:
            nid = n["node_id"]
            node_map[nid] = n

        # Track node statuses and outputs.
        # This allows restart/resume flows to preserve completed predecessors.
        node_statuses: Dict[str, str] = {}
        node_outputs: Dict[str, Dict[str, Any]] = {}
        for n in nodes_list:
            nid = n["node_id"]
            persisted_status = str(n.get("status") or "pending").lower()
            if persisted_status not in ("pending", "running", "completed", "failed", "skipped"):
                persisted_status = "pending"

            # Stale running nodes from a previous failed run should be retried.
            if persisted_status == "running":
                persisted_status = "pending"

            node_statuses[nid] = persisted_status
            persisted_output = n.get("output_data")
            if isinstance(persisted_output, dict) and persisted_status in ("completed", "skipped", "failed"):
                node_outputs[nid] = persisted_output

        await workflow.execute_activity(
            post_dag_progress,
            args=[dag_id, f"🚀 DAG execution started with {len(node_map)} nodes"],
            start_to_close_timeout=timedelta(seconds=15),
        )

        max_waves = len(node_map) + 5  # safety limit
        wave = 0
        failed_nodes = []

        # Track the richest capability-enriched image across waves so
        # downstream nodes inherit installed packages and file-system
        # state instead of starting from the bare base image.
        dag_current_image = ""

        while wave < max_waves:
            wave += 1
            abort_requested = False
            abort_reason = ""

            # Find nodes that are ready: all dependencies completed
            ready_nodes = []
            for nid, info in node_map.items():
                if node_statuses[nid] != "pending":
                    continue
                deps = info.get("depends_on", [])
                if all(node_statuses.get(d) == "completed" for d in deps):
                    # Check edge conditions for conditional deps
                    should_run = True
                    for dep in deps:
                        edge_ok = await workflow.execute_activity(
                            evaluate_edge_condition,
                            args=[dag_json, dep, nid, node_outputs],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        if not edge_ok:
                            should_run = False
                            break

                    if should_run:
                        ready_nodes.append(nid)
                    else:
                        node_statuses[nid] = "skipped"
                        await workflow.execute_activity(
                            update_node_status,
                            args=[dag_id, nid, "skipped"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )

            # Check if any nodes depend on failed nodes and should be skipped
            for nid, info in node_map.items():
                if node_statuses[nid] != "pending":
                    continue
                deps = info.get("depends_on", [])
                # If any dependency failed and no on_failure edge exists, skip this node
                for dep in deps:
                    if node_statuses.get(dep) == "failed":
                        # Check if there's an on_failure edge that allows continuation
                        has_failure_edge = False
                        for edge in dag_json.get("edges", []):
                            if edge.get("from") == dep and edge.get("to") == nid and edge.get("condition") == "on_failure":
                                has_failure_edge = True
                                break
                        if not has_failure_edge:
                            node_statuses[nid] = "skipped"
                            await workflow.execute_activity(
                                update_node_status,
                                args=[dag_id, nid, "skipped"],
                                start_to_close_timeout=timedelta(seconds=15),
                            )

            if not ready_nodes:
                # Check if we're done or stuck
                active = [nid for nid, s in node_statuses.items() if s in ("pending", "running")]
                if not active:
                    break
                # If nodes are still pending but none are ready, we're stuck (circular dep or all deps failed)
                pending = [nid for nid, s in node_statuses.items() if s == "pending"]
                if pending and not any(s == "running" for s in node_statuses.values()):
                    logger.warning(f"⚠️ DAG {dag_id} stuck — skipping remaining nodes: {pending}")
                    for nid in pending:
                        node_statuses[nid] = "skipped"
                        await workflow.execute_activity(
                            update_node_status,
                            args=[dag_id, nid, "skipped"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                    break
                # Some nodes might still be running — wait
                await workflow.sleep(5)
                continue

            await workflow.execute_activity(
                post_dag_progress,
                args=[dag_id, f"📋 Wave {wave}: launching {len(ready_nodes)} nodes: {ready_nodes}"],
                start_to_close_timeout=timedelta(seconds=15),
            )

            # Launch ready nodes in parallel as child workflows
            child_handles = []
            for nid in ready_nodes:
                node_info = node_map[nid]
                node_statuses[nid] = "running"

                # Build input data from input_mapping with strict resolution checks.
                input_data = {}
                input_mapping = node_info.get("input_mapping", {})
                resolution_report: Dict[str, Any] = {
                    "has_explicit_mapping": bool(input_mapping),
                    "resolved_inputs": [],
                    "literal_inputs": [],
                    "missing_required_inputs": [],
                    "dependency_inputs": [],
                    "missing_dependency_outputs": [],
                }

                for key, source_spec in input_mapping.items():
                    # Structured mapping support: {"from": "node-a", "optional": false}
                    if isinstance(source_spec, dict) and source_spec.get("from"):
                        source_node = str(source_spec.get("from"))
                        optional = bool(source_spec.get("optional", False))
                        if source_node in node_outputs:
                            input_data[key] = node_outputs[source_node]
                            resolution_report["resolved_inputs"].append({
                                "key": key,
                                "source_node": source_node,
                                "optional": optional,
                            })
                        elif not optional:
                            resolution_report["missing_required_inputs"].append({
                                "key": key,
                                "source_node": source_node,
                                "reason": "missing source node output",
                            })
                    elif isinstance(source_spec, str):
                        source_node = source_spec
                        source_field = None
                        if "." in source_spec:
                            source_node, source_field = source_spec.split(".", 1)

                        if source_node in node_outputs:
                            source_value = node_outputs[source_node]
                            if source_field:
                                if source_field == "output":
                                    source_value = _extract_task_output_text(source_value)
                                elif isinstance(source_value, dict) and source_field in source_value:
                                    source_value = source_value.get(source_field)
                                elif isinstance(source_value, dict) and source_value.get("deliverables") and source_field in source_value["deliverables"]:
                                    source_value = source_value["deliverables"][source_field]
                                else:
                                    source_value = None

                            if source_value is not None:
                                input_data[key] = source_value
                                resolution_report["resolved_inputs"].append({
                                    "key": key,
                                    "source_node": source_node,
                                    "source_field": source_field,
                                    "optional": False,
                                })
                            else:
                                resolution_report["missing_required_inputs"].append({
                                    "key": key,
                                    "source_node": source_spec,
                                    "reason": "missing source node field",
                                })
                        else:
                            resolution_report["missing_required_inputs"].append({
                                "key": key,
                                "source_node": source_spec,
                                "reason": "missing source node output",
                            })
                    else:
                        # Literal constants are valid explicit inputs.
                        input_data[key] = source_spec
                        resolution_report["literal_inputs"].append({
                            "key": key,
                            "value": source_spec,
                        })

                # If no explicit mapping, pass all dependency outputs.
                if not input_mapping:
                    for dep in node_info.get("depends_on", []):
                        if dep in node_outputs:
                            input_data[dep] = node_outputs[dep]
                            resolution_report["dependency_inputs"].append(dep)
                        else:
                            resolution_report["missing_dependency_outputs"].append(dep)

                # Bound the aggregated upstream input so activity/child-workflow
                # payloads stay under Temporal's size limit, even with many deps.
                input_data = _bounded_payload(input_data, max_str=20000, max_total=600000)

                if resolution_report["missing_dependency_outputs"]:
                    for dep in resolution_report["missing_dependency_outputs"]:
                        resolution_report["missing_required_inputs"].append({
                            "key": dep,
                            "source_node": dep,
                            "reason": "dependency marked completed but output missing",
                        })

                # Strict fail-fast: unresolved required inputs fail node immediately.
                if resolution_report["missing_required_inputs"]:
                    failure_reason = "unresolved required node inputs"
                    failure_output = {
                        "status": "failed",
                        "error": failure_reason,
                        "input_resolution": resolution_report,
                    }

                    node_statuses[nid] = "failed"
                    failed_nodes.append(nid)

                    await workflow.execute_activity(
                        update_node_status,
                        args=[dag_id, nid, "failed", failure_output],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    await workflow.execute_activity(
                        post_node_state_snapshot,
                        args=[
                            dag_id,
                            nid,
                            {
                                "phase": "input_resolution_failed",
                                "status": "failed",
                                "wave": wave,
                                "input_context": {
                                    "input_mapping": input_mapping,
                                    "resolved_input_data": input_data,
                                },
                                "completion_state": {
                                    "reason": failure_reason,
                                },
                                "acceptance_result": {
                                    "valid": False,
                                    "reason": failure_reason,
                                    "unresolved_inputs": resolution_report["missing_required_inputs"],
                                },
                                "pending_items": resolution_report["missing_required_inputs"],
                            },
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    await workflow.execute_activity(
                        post_node_audit_event,
                        args=[
                            dag_id,
                            nid,
                            {
                                "event_type": "input_resolution_failed",
                                "severity": "critical",
                                "message": failure_reason,
                                "event_data": resolution_report,
                            },
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    abort_requested = True
                    abort_reason = f"node '{nid}' failed: {failure_reason}"
                    continue

                await workflow.execute_activity(
                    post_node_state_snapshot,
                    args=[
                        dag_id,
                        nid,
                        {
                            "phase": "input_resolved",
                            "status": "ready",
                            "wave": wave,
                            "input_context": {
                                "input_mapping": input_mapping,
                                "resolved_input_data": input_data,
                            },
                            "completion_state": {
                                "dependencies": node_info.get("depends_on", []),
                                "resolved": True,
                            },
                            "pending_items": [],
                        },
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                node_config = dict(node_info.get("config", {}))
                generic_base_images = {"openclaw", "nanobot", "taskforge"}
                node_base = node_config.get("base_image", "openclaw")
                if node_base in generic_base_images and dag_current_image:
                    node_config["dag_image"] = dag_current_image

                child_params = {
                    "dag_id": dag_id,
                    "node_id": nid,
                    "description": node_info.get("description", ""),
                    "config": node_config,
                    "input_data": input_data,
                    "state_context": {
                        "wave": wave,
                        "input_resolution": resolution_report,
                        "upstream_state_review_required": bool(input_data),
                        "completed_nodes": [k for k, v in node_statuses.items() if v == "completed"],
                        "failed_nodes": [k for k, v in node_statuses.items() if v == "failed"],
                        "pending_nodes": [k for k, v in node_statuses.items() if v == "pending"],
                    },
                    "workspace_id": dag_workspace_id,
                }

                handle = await workflow.start_child_workflow(
                    DAGNodeWorkflow.run,
                    child_params,
                    id=f"dag-node-{dag_id}-{nid}",
                )
                child_handles.append((nid, handle))

            # Wait for all children in this wave
            for nid, handle in child_handles:
                try:
                    result = await handle
                    node_statuses[nid] = result.get("status", "failed")
                    if result.get("output"):
                        node_outputs[nid] = result["output"]
                    if node_statuses[nid] == "failed":
                        failed_nodes.append(nid)
                        abort_requested = True
                        abort_reason = result.get("gate_failure") or result.get("error") or f"node '{nid}' failed"

                    # Propagate the committed/enriched image to subsequent waves
                    # so downstream nodes inherit the file-system state.
                    _node_img = result.get("current_image", "")
                    if _node_img:
                        dag_current_image = _node_img
                        logger.info(
                            f"🔗 DAG image updated from node {nid}: {dag_current_image}"
                        )
                except Exception as e:
                    logger.error(f"❌ Child workflow for node {nid} failed: {e}")
                    node_statuses[nid] = "failed"
                    failed_nodes.append(nid)
                    node_outputs[nid] = {"error": str(e), "status": "failed"}
                    abort_requested = True
                    abort_reason = str(e)

            # Fail-fast gate: stop scheduling new work when any node fails.
            if abort_requested:
                pending_nodes = [nid for nid, s in node_statuses.items() if s == "pending"]
                for pending_id in pending_nodes:
                    node_statuses[pending_id] = "skipped"
                    await workflow.execute_activity(
                        update_node_status,
                        args=[dag_id, pending_id, "skipped"],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                await workflow.execute_activity(
                    post_dag_progress,
                    args=[dag_id, f"🛑 DAG halted after wave {wave}: {abort_reason}"],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                break

        # Determine final DAG status
        completed = sum(1 for s in node_statuses.values() if s == "completed")
        failed = sum(1 for s in node_statuses.values() if s == "failed")
        skipped = sum(1 for s in node_statuses.values() if s == "skipped")
        total = len(node_statuses)

        if failed > 0:
            final_status = "failed"
        elif completed == total:
            final_status = "completed"
        elif completed + skipped == total:
            final_status = "completed"
        else:
            final_status = "failed"

        await workflow.execute_activity(
            finalize_dag,
            args=[dag_id, final_status],
            start_to_close_timeout=timedelta(seconds=15),
        )

        summary = f"DAG {dag_id} {final_status}: {completed}/{total} completed, {failed} failed, {skipped} skipped"
        await workflow.execute_activity(
            post_dag_progress,
            args=[dag_id, f"🏁 {summary}"],
            start_to_close_timeout=timedelta(seconds=15),
        )

        logger.info(f"🏁 DAGWorkflow finished | {summary}")

        return {
            "dag_id": dag_id,
            "status": final_status,
            "nodes_executed": completed,
            "nodes_failed": failed,
            "nodes_skipped": skipped,
            "node_statuses": node_statuses,
        }


# =============================================================================
# Deployment Workflows
# =============================================================================

@workflow.defn
class DeploymentBuildWorkflow:
    """Workflow to build a deployment image after approval."""

    @workflow.run
    async def run(self, deployment_id: str) -> Dict[str, Any]:
        logger.info(f"DeploymentBuildWorkflow started for {deployment_id}")

        result = await workflow.execute_activity(
            build_deployment_image,
            args=[deployment_id],
            start_to_close_timeout=timedelta(minutes=10),
        )

        return result


@workflow.defn
class DeploymentRunWorkflow:
    """Workflow to start or stop a deployment container."""

    @workflow.run
    async def run(self, deployment_id: str, action: str = "start") -> Dict[str, Any]:
        logger.info(f"DeploymentRunWorkflow: {action} {deployment_id}")

        if action == "start":
            result = await workflow.execute_activity(
                start_deployment_container,
                args=[deployment_id],
                start_to_close_timeout=timedelta(minutes=5),
            )
        elif action == "stop":
            result = await workflow.execute_activity(
                stop_deployment_container,
                args=[deployment_id],
                start_to_close_timeout=timedelta(minutes=2),
            )
        else:
            result = {"error": f"Unknown action: {action}"}

        return result


# =============================================================================
# Worker
# =============================================================================

async def main():
    """Main worker entry point"""
    
    logger.info(f"Connecting to Temporal at {TEMPORAL_HOST}")
    
    # Connect to Temporal
    client = await Client.connect(TEMPORAL_HOST)
    
    # Create worker
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentTaskWorkflow, AgentStepWorkflow, DeploymentBuildWorkflow, DeploymentRunWorkflow, DAGWorkflow, DAGNodeWorkflow],
        activities=[
            initialize_task,
            start_agent_container,
            poll_agent_turns,
            collect_agent_result,
            record_agent_turn,
            store_task_output,
            get_last_iteration,
            create_capability_request,
            list_task_capability_requests,
            dismiss_pending_capabilities,
            build_agent_image,
            update_task_policy,
            add_to_supply_chain,
            reload_supply_chain,
            check_verdict_guard,
            finalize_task,
            create_deployment,
            build_deployment_image,
            start_deployment_container,
            stop_deployment_container,
            check_deploy_authority,
            trial_deploy,
            # DAG activities
            load_dag,
            update_node_status,
            update_dag_status,
            post_dag_progress,
            post_node_state_snapshot,
            post_node_audit_event,
            post_node_structured_output,
            create_node_task,
            collect_node_output,
            evaluate_node_gate,
            evaluate_edge_condition,
            finalize_dag,
            persist_task_workflow_id,
            update_task_status,
        ],
    )
    
    logger.info(f"Worker starting on task queue: {TASK_QUEUE}")
    
    # Run worker
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

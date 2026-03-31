"""
Temporal Worker - Executes workflows and activities
"""
import asyncio
import logging
import re
from temporalio import workflow, activity
from .worker_api import get_task_current_image
from temporalio.client import Client
from temporalio.worker import Worker
from datetime import timedelta, datetime
from typing import Dict, Any, Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "openclaw-tasks")

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
    
    @workflow.run
    async def run(
        self,
        task_id: str,
        llm_model: str = "gemma3:4b",
        current_image: str = "",
        follow_up: str = "",
    ) -> Dict[str, Any]:
        """Execute agent task.

        For first-run workflows ``current_image`` can be a base image tag
        (e.g. ``localhost:5000/openclaw-agent:zeroclaw``) or empty for the
        default openclaw image.
        For continuation workflows it carries over from the previous run:
        - ``current_image``: the last built agent image (all packages installed)
        - ``follow_up``: user's follow-up instructions
        """
        
        self.llm_model = llm_model
        self.follow_up = follow_up

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
                args=[task_id, iteration, self.current_image, self.llm_model, iter_follow_up],
                id=f"agent-step-{task_id}-iter-{iteration}",
            )

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

                # If this task is part of a Task Force, check if the role
                # is allowed to deploy (only the lead / developer can deploy).
                should_deploy = True
                tf_role_info = await workflow.execute_activity(
                    check_task_force_deploy_role,
                    args=[task_id],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                if tf_role_info.get("is_task_force") and not tf_role_info.get("can_deploy"):
                    logger.info(
                        f"🚫 DEPLOY_SUPPRESSED | {task_id} role={tf_role_info.get('role')} "
                        f"— deployment handled by {tf_role_info.get('lead_role')} only"
                    )
                    should_deploy = False

                if should_deploy:
                    # Create deployment record via control plane
                    deploy_result = await workflow.execute_activity(
                        create_deployment,
                        args=[task_id, deployment],
                        start_to_close_timeout=timedelta(seconds=30)
                    )
                    logger.info(f"📦 Deployment created: {deploy_result.get('id')}")
                break
            
            # Check if task complete
            if result.get("completed"):
                break
            
            # Check if capability requested
            if result.get("capability_requested"):
                capability = result.get("capability")
                
                logger.info(f"Capability requested: {capability}")
                
                # Create capability request
                await workflow.execute_activity(
                    create_capability_request,
                    args=[task_id, capability],
                    start_to_close_timeout=timedelta(seconds=30)
                )
                
                # Wait for approval signal (workflow pauses here)
                await workflow.wait_condition(
                    lambda: self.approval_received,
                    timeout=timedelta(hours=24)
                )
                
                if self.capability_approved:
                    # Build new image with capability — use current_image as base
                    # so each version layers on top of the previous (v1 → v2 → v3)
                    build_result = await workflow.execute_activity(
                        build_agent_image,
                        args=[task_id, capability, self.current_image],
                        start_to_close_timeout=timedelta(minutes=10)
                    )
                    
                    # build_result is a dict: {image, feedback, denied}
                    new_image = build_result.get("image", self.current_image)
                    supply_chain_feedback = build_result.get("feedback", "")

                    # Update current image for subsequent iterations
                    self.current_image = new_image
                    logger.info(f"Updated task image to {new_image}")

                    # If the supply chain denied any packages, inject feedback
                    # into the follow-up so the agent learns what's unavailable.
                    if supply_chain_feedback:
                        logger.warning(f"🚫 Supply-chain feedback for agent: {supply_chain_feedback[:200]}")
                        self._capability_feedback = (
                            "--- SYSTEM NOTICE ---\n"
                            + supply_chain_feedback
                            + "\n--- END NOTICE ---"
                        )
                    
                    # Update policy
                    await workflow.execute_activity(
                        update_task_policy,
                        args=[task_id, capability, new_image],
                        start_to_close_timeout=timedelta(seconds=30)
                    )
                    
                    logger.info(f"Task {task_id} resumed with new capability")
                else:
                    logger.info(f"Capability request denied for task {task_id}")
                    # Tell the agent its capability request was denied by the user
                    self._capability_feedback = (
                        "--- SYSTEM NOTICE ---\n"
                        + f"CAPABILITY_DENIED: Your request for '{capability.get('resource', '')}' "
                        + "was denied by the operator. Find an alternative approach.\n"
                        + "--- END NOTICE ---"
                    )
                
                # Reset approval flags
                self.approval_received = False
                self.capability_approved = False

                # --- Verdict guard: if this task already wrote a PASS verdict,
                # stop iterating.  Prevents post-verdict workspace corruption
                # after capability rebuilds. ---
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

        # Step 3: Finalize task
        final_result = await workflow.execute_activity(
            finalize_task,
            args=[task_id],
            start_to_close_timeout=timedelta(minutes=5)
        )
        
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
            args=[task_id, iteration, container_id, workspace_dir, agent_image, llm_model],
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
        task_force_id = ""
        try:
            import httpx as _httpx
            _cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            async with _httpx.AsyncClient(timeout=10.0) as _client:
                _resp = await _client.get(f"{_cp_url}/api/tasks/{task_id}")
                if _resp.status_code == 200:
                    _task_data = _resp.json()
                    workspace_id = _task_data.get("workspace_id", "")
                    task_description = _task_data.get("description", "")
                    task_force_id = _task_data.get("task_force_id", "") or ""
        except Exception as _e:
            logger.warning(f"⚠️ Could not fetch task details: {_e}")

        if not workspace_id:
            workspace_id = f"workspace-{task_id}"

        # --- pre-installed packages discovery ---
        # Query approved capability requests so the agent knows what's
        # already baked into its image and doesn't re-request them.
        pre_installed_packages = ""
        try:
            import httpx as _httpx2
            _cp2 = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            async with _httpx2.AsyncClient(timeout=10.0) as _hc2:
                # Query by task_force_id if available, otherwise by task_id
                _q = f"task_force_id={task_force_id}" if task_force_id else f"task_id={task_id}"
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
        # Session ID: task-force scoped by role so memory persists across
        # rework cycles for the same agent role.  For standalone tasks we
        # fall back to the task_id.
        zep_session_id = ""
        if task_force_id:
            _role = agent_image.split("/")[-1].split(":")[0]  # e.g. "openclaw-agent" – not useful
            # Use the task's role from API (member_role field) if available
            try:
                import httpx as _httpx_zep
                _cp_zep = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
                async with _httpx_zep.AsyncClient(timeout=10.0) as _hcz:
                    _tz = await _hcz.get(f"{_cp_zep}/api/tasks/{task_id}")
                    if _tz.status_code == 200:
                        _td = _tz.json()
                        _member_role = _td.get("task_force_role", "") or _td.get("role", "") or "agent"
                        zep_session_id = f"{task_force_id}_{_member_role}"
            except Exception:
                zep_session_id = f"{task_force_id}_agent"
            if not zep_session_id:
                zep_session_id = f"{task_force_id}_agent"
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
        }


        container_kwargs = dict(
            image=agent_image,
            environment=agent_env,
            volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
            tmpfs={"/tmp": "size=100m,mode=1777"},
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
    for _ in range(max_polls):
        activity.heartbeat(f"turns_seen={turns_seen + len(new_turns)}")

        # Check for new interactions from the LLM router
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
                        new_turns.extend(batch)
                        logger.info(f"📡 Got {len(batch)} new turn(s) for {task_id} (total seen: {turns_seen + len(new_turns)})")
        except Exception as e:
            logger.warning(f"⚠️ Poll interactions failed: {e}")

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
) -> Dict[str, Any]:
    """Collect the final result from the stopped agent container.

    Reads the result from stdout markers or result.json, fetches any
    remaining LLM interactions, and cleans up the container.
    """
    import docker
    import json as json_lib
    import httpx

    logger.info(f"📦 COLLECT_RESULT | Task: {task_id} | Iter: {iteration}")

    docker_client = get_docker_client()
    cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    try:
        container = docker_client.containers.get(container_id)

        # Wait for exit (should already be done, but just in case)
        exit_info = container.wait(timeout=120)
        exit_code = exit_info.get("StatusCode", -1)

        container_output = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        logger.info(f"📄 Container exited with code {exit_code}, output ({len(container_output)} bytes)")

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

        result["agent_logs"] = container_output[:50000]
        result["_temporal_metadata"] = {
            "task_id": task_id,
            "iteration": iteration,
            "image": agent_image,
            "timestamp": str(datetime.now()),
        }
        result["_remaining_turns"] = remaining_turns
        return result

    # Fallback: no structured result
    logger.warning("⚠️ No result markers or file found, attempting raw parse")
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
        "output": container_output[:50000],
        "agent_logs": container_output[:50000],
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

    payload = {
        "task_id": task_id,
        "iteration": iteration,
        "completed": str(result.get("completed", False)).lower(),
        "capability_requested": str(result.get("capability_requested", False)).lower(),
        "agent_logs": result.get("agent_logs", "")[:50000],
        "output": output_str[:50000] if isinstance(output_str, str) else str(output_str)[:50000],
        "error": result.get("error"),
        "llm_response_preview": result.get("message", "")[:500],
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
        # Split comma-separated resources into individual capabilities
        resources = [r.strip() for r in resource.split(",") if r.strip()]
        build_capabilities = [
            {
                "type": "pip_package" if cap_type == "tool_install" else cap_type,
                "name": r,
                "version": None
            }
            for r in resources
        ]
        
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
async def check_task_force_deploy_role(task_id: str) -> Dict[str, Any]:
    """Check whether a TF member task's role is allowed to deploy.

    Only the 'lead' role (Developer, Lead, Architect, etc.) can deploy.
    Support roles (Tester, Reviewer, QA, etc.) are suppressed.
    Returns {is_task_force, can_deploy, role, lead_role}.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
        if resp.status_code != 200:
            return {"is_task_force": False, "can_deploy": True}

        task_data = resp.json()
        tf_id = task_data.get("task_force_id")
        if not tf_id:
            return {"is_task_force": False, "can_deploy": True}

        role = (task_data.get("task_force_role") or "").lower()
        support_roles = {"tester", "qa", "auditor", "validator"}

        # Find the lead role from the task force — whoever has the
        # highest execution_order is the "deploy lead" (typically the
        # Reviewer or Deployer).  Fall back to the first Developer.
        tf_resp = await client.get(f"{control_plane_url}/api/task-forces/{tf_id}")
        lead_role = "Developer"
        if tf_resp.status_code == 200:
            tf_data = tf_resp.json()
            members = tf_data.get("members", [])
            # Highest execution_order member is the deploy lead
            if members:
                last_member = max(members, key=lambda m: m.get("execution_order", 0))
                lead_role = last_member.get("role", "Developer")

        return {
            "is_task_force": True,
            "can_deploy": role not in support_roles,
            "role": task_data.get("task_force_role", ""),
            "lead_role": lead_role,
        }


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
# Task Force Activities — Multi-Agent Orchestration
# =============================================================================

@activity.defn
async def post_task_force_progress(
    task_force_id: str,
    message: str,
) -> bool:
    """Post a progress message to the Task Force coordinator task.

    Finds the coordinator task (task_force_role='coordinator') and
    creates a TaskMessage so progress is visible on the task detail page.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Find coordinator task
        resp = await client.get(
            f"{control_plane_url}/api/tasks",
            params={"limit": 200},
        )
        if resp.status_code != 200:
            return False

        tasks = resp.json()
        coord_task_id = None
        for t in tasks:
            if (t.get("agent_profile") == task_force_id
                    and "coordinator" in (t.get("name", "") + t.get("description", "")).lower()
                    or t.get("agent_profile") == task_force_id):
                # Use the first task with this task_force as agent_profile
                coord_task_id = t["id"]
                break

        if not coord_task_id:
            logger.warning(f"⚠️ Could not find coordinator task for {task_force_id}")
            return False

        # Post a message to the coordinator task
        msg_resp = await client.post(
            f"{control_plane_url}/api/tasks/{coord_task_id}/messages",
            json={"content": message, "role": "system"},
        )
        return msg_resp.status_code in (200, 201)


@activity.defn
async def update_coordinator_task_status(
    task_force_id: str,
    status: str,
) -> bool:
    """Update the coordinator task status to completed/failed."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{control_plane_url}/api/tasks",
            params={"limit": 200},
        )
        if resp.status_code != 200:
            return False

        tasks = resp.json()
        for t in tasks:
            if t.get("agent_profile") == task_force_id:
                task_id = t["id"]
                endpoint = "complete" if status == "completed" else "fail"
                await client.post(f"{control_plane_url}/api/tasks/{task_id}/{endpoint}")
                return True
    return False


@activity.defn
async def load_task_force(task_force_id: str) -> Dict[str, Any]:
    """Load Task Force definition including members and ceremonies from the control plane."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{control_plane_url}/api/task-forces/{task_force_id}")
        if resp.status_code != 200:
            raise Exception(f"Failed to load Task Force {task_force_id}: HTTP {resp.status_code}")
        return resp.json()


@activity.defn
async def start_member_task(task_id: str, llm_model: str, base_image: str) -> str:
    """Start a Temporal AgentTaskWorkflow for a single Task Force member."""
    import httpx
    from temporalio.client import Client as TClient

    client = await TClient.connect(TEMPORAL_HOST)
    workflow_id = f"task-workflow-{task_id}"

    await client.start_workflow(
        "AgentTaskWorkflow",
        args=[task_id, llm_model, base_image],
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    logger.info(f"🚀 TASK_FORCE_MEMBER | Started workflow {workflow_id}")

    # Persist workflow_id back to the control-plane so that capability
    # approval signals can be routed to this specific workflow.
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.patch(
                f"{control_plane_url}/api/tasks/{task_id}",
                json={"workflow_id": workflow_id},
            )
        logger.info(f"✅ Saved workflow_id {workflow_id} to task {task_id}")
    except Exception as e:
        logger.warning(f"Could not persist workflow_id for {task_id}: {e}")

    return workflow_id


@activity.defn
async def get_task_current_image(task_id: str) -> Optional[str]:
    """Fetch the task's current_image from the control plane."""
    import httpx
    import os
    from temporalio import activity

    _cp = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as _hc:
            _tr = await _hc.get(f"{_cp}/api/tasks/{task_id}")
            _tr.raise_for_status()
            db_image = _tr.json().get("current_image", "")
            if db_image:
                activity.logger.info(
                    f"✅ Fetched DB image for {task_id}: {db_image}"
                )
                return db_image
    except Exception as e:
        activity.logger.warning(
            f"Could not fetch current_image for {task_id}: {e}"
        )
    activity.logger.info(f"❌ No DB image found for {task_id}, returning None")
    return None


@activity.defn
async def persist_member_workflow_id(task_id: str, workflow_id: str) -> bool:
    """Persist a child-workflow ID to the control-plane DB.

    This keeps the control-plane aware of workflows started natively as
    Temporal child workflows (instead of via the old ``start_member_task``
    activity).  The stored ``workflow_id`` is used to route capability-
    approval signals.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{control_plane_url}/api/tasks/{task_id}",
                json={"workflow_id": workflow_id},
            )
            if resp.status_code < 300:
                logger.info(f"✅ Persisted workflow_id {workflow_id} → task {task_id}")
                return True
            logger.warning(f"persist_member_workflow_id: HTTP {resp.status_code} for {task_id}")
            return False
    except Exception as e:
        logger.warning(f"persist_member_workflow_id failed for {task_id}: {e}")
        return False


@activity.defn
async def update_member_status(task_force_id: str, member_id: int, status: str) -> bool:
    """Update a Task Force member's status via the control plane."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Update the task that belongs to this member
        resp = await client.get(f"{control_plane_url}/api/task-forces/{task_force_id}")
        if resp.status_code != 200:
            return False
        tf_data = resp.json()
        for m in tf_data.get("members", []):
            if m["id"] == member_id and m.get("task_id"):
                task_resp = await client.get(f"{control_plane_url}/api/tasks/{m['task_id']}")
                if task_resp.status_code == 200:
                    task_status = task_resp.json().get("status", "unknown")
                    logger.info(f"📊 Member {member_id} task status: {task_status}")
                    return task_status in ("completed", "failed")
    return False


@activity.defn
async def poll_member_task_status(task_id: str) -> Dict[str, Any]:
    """Poll a member's task to check if it's completed."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
        if resp.status_code != 200:
            return {"status": "unknown", "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        return {
            "status": data.get("status", "unknown"),
            "task_id": task_id,
        }


@activity.defn
async def write_ceremony_artifact(
    workspace_id: str,
    filename: str,
    content: str,
    task_force_id: str = "",
    ceremony_id: int = 0,
    artifact_kind: str = "custom",
    task_id: str = "",
    rework_cycle: int = 0,
) -> Dict[str, Any]:
    """Write a ceremony artifact to workspace AND to the ceremony state API.

    The workspace file is kept for backward compatibility (agents read from
    /workspace), but the API record is the source of truth for verdicts and
    audit trails.
    """
    workspaces_root = "/workspaces"
    workspace_dir = os.path.join(workspaces_root, workspace_id)
    os.makedirs(workspace_dir, exist_ok=True)

    filepath = os.path.join(workspace_dir, filename)
    with open(filepath, "w") as f:
        f.write(content)
    os.chmod(filepath, 0o666)

    logger.info(f"📝 CEREMONY ARTIFACT | Written: {filepath} ({len(content)} bytes)")

    # Also store in the ceremony state API for traceability
    api_id = None
    if task_force_id:
        import httpx
        control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{control_plane_url}/api/task-forces/{task_force_id}/artifacts",
                    json={
                        "kind": artifact_kind,
                        "ceremony_id": ceremony_id or None,
                        "filename": filename,
                        "title": filename.replace("_", " ").replace(".md", ""),
                        "content": content[:64000],  # cap at 64k for DB
                        "rework_cycle": rework_cycle,
                    },
                )
                if resp.status_code in (200, 201):
                    api_id = resp.json().get("id")
                    logger.info(f"📝 Artifact also stored in API: #{api_id}")
                else:
                    logger.warning(f"⚠️ Artifact API store failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"⚠️ Could not store artifact via API: {e}")

    return {"filepath": filepath, "filename": filename, "size": len(content), "api_id": api_id}


@activity.defn
async def generate_ceremony_plan(
    objective: str,
    team_info: str,
    ceremony_info: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """Use the LLM to generate a structured ceremony plan.

    Calls the control-plane LLM router to produce a work plan
    that coordinates agent activities.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    prompt = f"""You are a technical project coordinator. Generate a structured work plan for the following task force.

## Objective
{objective}

## Team
{team_info}

## Ceremony Context
{ceremony_info}

## Instructions
Create a DETAILED work plan in Markdown format that:
1. Breaks the objective into clear phases with owners (which role does what)
2. Specifies what files/deliverables each team member should produce
3. Defines handoff points — when one role finishes, what artifact does the next role pick up
4. Lists acceptance criteria for each phase
5. Specifies which team member is the deployment lead (who creates deployment requests)

Output ONLY the Markdown plan, no preamble.
"""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/llm/v1/chat/completions",
                json={
                    "model": "gemini-flash-latest",
                    "messages": [
                        {"role": "system", "content": "You are a technical project coordinator producing structured work plans."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
                headers={"Authorization": "Bearer task:ceremony-planner"},
            )
            if resp.status_code == 200:
                data = resp.json()
                plan_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if plan_text:
                    logger.info(f"📋 CEREMONY PLAN | Generated {len(plan_text)} chars")
                    return {"plan": plan_text, "status": "generated"}

            logger.warning(f"⚠️ LLM plan generation returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"⚠️ LLM plan generation failed: {e}")

    # Fallback: produce a basic plan without LLM
    fallback = f"""# Work Plan

## Objective
{objective}

## Team
{team_info}

## Phases
1. **Development Phase** — Lead developer implements the solution
2. **Review Phase** — Reviewer/Tester examines the deliverables
3. **Finalization** — Lead incorporates feedback and prepares deployment

## Coordination
- Lead developer owns the deployment request
- All deliverables should be placed in the shared workspace
- Each member writes a DONE_<ROLE>.md file when finished
"""
    return {"plan": fallback, "status": "fallback"}


@activity.defn
async def read_verdict_file(
    workspace_id: str,
    verdict_file: str,
    task_force_id: str = "",
    rework_cycle: int = 0,
) -> Dict[str, Any]:
    """Read a verdict — first from the ceremony state API, then fall back to
    the workspace file.

    The API is the authoritative source: verdicts submitted via
    ``POST /api/tasks/{task_id}/verdict`` are immutable and can't be
    overwritten by rogue agent iterations.

    Falls back to filesystem scan for backward compatibility with agents
    that haven't been updated to use the verdict API yet.

    Returns ``{"verdict": "pass"|"fail"|"unknown", "content": "<text>", "source": "api"|"file"}``
    """
    import re

    # --- 1. Try API first (authoritative) ---
    if task_force_id:
        import httpx
        control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {}
                if rework_cycle is not None:
                    params["rework_cycle"] = rework_cycle
                resp = await client.get(
                    f"{control_plane_url}/api/task-forces/{task_force_id}/verdict",
                    params=params,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    api_verdict = data.get("verdict", "unknown")
                    logger.info(
                        f"⚖️ Verdict from API: {api_verdict.upper()} "
                        f"(tf={task_force_id}, cycle={rework_cycle})"
                    )
                    return {
                        "verdict": api_verdict,
                        "content": data.get("summary", ""),
                        "source": "api",
                        "artifact_id": data.get("id"),
                    }
                elif resp.status_code == 404:
                    logger.info("No API verdict found — falling back to file")
                else:
                    logger.warning(f"⚠️ Verdict API returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Verdict API query failed: {e} — falling back to file")

    # --- 2. Fall back to workspace file ---
    workspaces_root = "/workspaces"
    filepath = os.path.join(workspaces_root, workspace_id, verdict_file)

    if not os.path.isfile(filepath):
        logger.warning(f"Verdict file not found: {filepath}")
        return {"verdict": "unknown", "content": "", "error": "file_not_found", "source": "file"}

    with open(filepath, "r", errors="replace") as f:
        content = f.read(16384)

    # Try JSON first
    try:
        import json as _json
        data = _json.loads(content)
        v = str(data.get("verdict", "")).strip().lower()
        if v in ("pass", "fail"):
            return {"verdict": v, "content": content, "source": "file"}
    except Exception:
        pass

    # Regex scan for verdict markers
    fail_pattern = re.compile(
        r"\b(FAIL|FAILED|REJECTED|REWORK\s*NEEDED)\b", re.IGNORECASE
    )
    pass_pattern = re.compile(
        r"\b(PASS|PASSED|APPROVED)\b", re.IGNORECASE
    )

    has_fail = bool(fail_pattern.search(content))
    has_pass = bool(pass_pattern.search(content))

    if has_fail and not has_pass:
        verdict = "fail"
    elif has_pass and not has_fail:
        verdict = "pass"
    elif has_fail and has_pass:
        # Both present — look at the last occurrence to decide
        last_fail = max(m.start() for m in fail_pattern.finditer(content))
        last_pass = max(m.start() for m in pass_pattern.finditer(content))
        verdict = "fail" if last_fail > last_pass else "pass"
    else:
        verdict = "unknown"

    logger.info(f"📝 Verdict from {verdict_file}: {verdict.upper()} (source=file)")

    return {"verdict": verdict, "content": content, "source": "file"}


# === Ceremony State API Activities ===
@activity.defn
async def post_agent_state_exchange(
    task_force_id: str,
    from_task_id: str,
    state_type: str,
    subject: str = "",
    body: str = "",
    state_data: dict = None,
    to_task_id: str = None,
) -> dict:
    """Post a state exchange message to the ceremony state API."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    payload = {
        "from_task_id": from_task_id,
        "to_task_id": to_task_id,
        "state_type": state_type,
        "subject": subject,
        "body": body,
        "state_data": state_data or {},
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{control_plane_url}/api/task-forces/{task_force_id}/state",
                json=payload,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.warning(f"⚠️ State exchange API failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"⚠️ Could not post state exchange: {e}")
    return {"error": "failed"}


@activity.defn
async def check_verdict_guard(
    task_id: str,
) -> dict:
    """Check if a PASS verdict already exists for this task's task force.

    Called after capability rebuilds to prevent the agent from continuing
    to iterate (and potentially corrupting the workspace) after it has
    already submitted a PASS verdict in a previous iteration.

    Looks up the task's task_force_id from the control-plane, then queries
    the ceremony state API for an existing verdict.  Returns early with
    ``{"verdict": "unknown"}`` for standalone tasks (no task force).
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Get task to find task_force_id
            task_resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
            if task_resp.status_code != 200:
                return {"verdict": "unknown"}
            task_data = task_resp.json()
            tf_id = task_data.get("task_force_id")
            if not tf_id:
                return {"verdict": "unknown"}  # standalone task — no guard needed

            # 2. Check for existing PASS verdict in the task force
            resp = await client.get(
                f"{control_plane_url}/api/task-forces/{tf_id}/verdict",
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("verdict") == "pass":
                    logger.info(
                        f"⚖️ Verdict guard: PASS already exists for tf={tf_id} "
                        f"(artifact #{data.get('id')})"
                    )
                    return {"verdict": "pass", "artifact_id": data.get("id")}
    except Exception as e:
        logger.warning(f"⚠️ Verdict guard API failed: {e}")
    return {"verdict": "unknown"}


@activity.defn
async def create_rework_tasks(
    task_force_id: str,
    members: list,
    target_order: int,
    workspace_id: str,
    feedback_content: str,
    cycle: int,
) -> Dict[str, Any]:
    """Create fresh tasks for members at ``execution_order >= target_order``.

    Writes ``REWORK_FEEDBACK_CYCLE_<N>.md`` to the workspace so new agents
    pick up the reviewer's notes, then creates new Task rows via the
    control-plane API.

    Returns ``{"new_members": [<updated member dicts with new task_ids>]}``.
    """
    import httpx

    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    workspaces_root = "/workspaces"

    # 1. Write feedback to workspace
    feedback_path = os.path.join(
        workspaces_root, workspace_id, f"REWORK_FEEDBACK_CYCLE_{cycle}.md"
    )
    os.makedirs(os.path.dirname(feedback_path), exist_ok=True)
    with open(feedback_path, "w") as f:
        f.write(f"# Rework Feedback — Cycle {cycle}\n\n")
        f.write(feedback_content)
    logger.info(f"📝 Wrote rework feedback to {feedback_path}")

    # 2. Identify members that need new tasks
    effective_target = target_order if target_order is not None else 0
    target_members = [
        m for m in members
        if m.get("execution_order", 0) >= effective_target
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Load current task force data to get member details
        tf_resp = await client.get(f"{control_plane_url}/api/task-forces/{task_force_id}")
        if tf_resp.status_code != 200:
            raise Exception(f"Failed to load TF: HTTP {tf_resp.status_code}")
        tf_data = tf_resp.json()
        tf_members = {m["id"]: m for m in tf_data.get("members", [])}
        tf_obj = tf_data.get("objective", "")
        tf_name = tf_data.get("name", "")
        all_members = tf_data.get("members", [])

        updated = []
        for member in target_members:
            member_id = member.get("id")
            db_member = tf_members.get(member_id, member)
            role = db_member.get("role", "Agent")
            responsibilities = db_member.get("responsibilities", "")
            llm_model = db_member.get("llm_model") or "gemini-flash-latest"
            base_image_key = db_member.get("base_image") or "openclaw"
            base_image = f"localhost:5000/openclaw-agent:{base_image_key}"
            agent_profile = db_member.get("agent_profile", "general-assistant")

            logger.info(f"DEBUG: Initial base_image for {member_id}: {base_image}")

            # ── Inherit the previous task's image (preserves capability builds) ──
            old_task_id = db_member.get("task_id")
            inherited_image = None
            if old_task_id:
                try:
                    old_resp = await client.get(
                        f"{control_plane_url}/api/tasks/{old_task_id}"
                    )
                    if old_resp.status_code == 200:
                        old_image = old_resp.json().get("current_image", "")
                        if old_image:
                            inherited_image = old_image
                            logger.info(
                                f"♻️  Inheriting image from {old_task_id} → "
                                f"{inherited_image} (for {role} rework)"
                            )
                except Exception as img_err:
                    logger.warning(
                        f"⚠️ Could not fetch image for {old_task_id}: {img_err}"
                    )

            import uuid
            new_task_id = f"task-{str(uuid.uuid4())[:8]}"

            team_roster = "\n".join(
                f"- **{m.get('role', '?')}** (order {m.get('execution_order', 0)})"
                for m in all_members
            )

            description = (
                f"## TASK FORCE OBJECTIVE\n{tf_obj}\n\n"
                f"## YOUR ROLE: {role}\n{responsibilities}\n\n"
                f"## REWORK CYCLE {cycle}\n"
                f"This is a **rework iteration**. A reviewer has requested changes.\n"
                f"Read `REWORK_FEEDBACK_CYCLE_{cycle}.md` in the workspace for details.\n"
                f"Also review `REVIEW_BRIEF.md` for the full review.\n\n"
                f"## TEAM COMPOSITION\n"
                f"You are part of **{tf_name}** with {len(all_members)} agents:\n"
                f"{team_roster}\n\n"
                f"## COORDINATION RULES\n"
                f"- Check the workspace for files from previous cycles and teammates.\n"
                f"- When done, write a summary to `/workspace/DONE_{role.upper().replace(' ', '_')}.md`\n"
                f"- Focus strictly on your role: **{role}**.\n"
            )

            # Create task via API (auto_start=false; workflow starts it later)
            task_payload = {
                "name": f"[{tf_name}] {role} (rework cycle {cycle})",
                "description": description,
                "workspace_id": workspace_id,
                "llm_model": llm_model,
                "base_image": base_image_key,
                "agent_profile": agent_profile,
                "task_force_id": task_force_id,
                "task_force_role": role,
                "auto_start": False,
            }

            resp = await client.post(
                f"{control_plane_url}/api/tasks",
                json=task_payload,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"Failed to create rework task for {role}: {resp.text}")
                continue

            new_task = resp.json()
            actual_task_id = new_task.get("id", new_task_id)

            # Carry over the previous task's image so the rework starts
            # from the already-built image (with packages etc.)
            if inherited_image:
                try:
                    await client.patch(
                        f"{control_plane_url}/api/tasks/{actual_task_id}/image",
                        json={"current_image": inherited_image},
                    )
                    logger.info(
                        f"✅ Rework task {actual_task_id} image set to {inherited_image}"
                    )
                except Exception as patch_err:
                    logger.warning(
                        f"⚠️ Could not set inherited image on {actual_task_id}: {patch_err}"
                    )

            # Update member's task_id in DB
            await client.patch(
                f"{control_plane_url}/api/task-forces/{task_force_id}/members/{member_id}",
                json={"task_id": actual_task_id, "status": "created"},
            )

            new_member = dict(member)
            new_member["task_id"] = actual_task_id
            updated.append(new_member)

            logger.info(
                f"🔄 Rework task created: {actual_task_id} for {role} "
                f"(cycle {cycle}, order {member.get('execution_order', 0)})"
            )

    return {"new_members": updated}


@activity.defn
async def collect_member_outputs(
    task_force_id: str,
    member_task_outputs: Dict[str, str],
    workspace_id: str,
) -> Dict[str, Any]:
    """Collect a lightweight *manifest* of member workspace files.

    Returns role → {task_id, workspace_path, files: [{filename, size}]}.
    **No file content** is included — keeps the Temporal payload tiny even
    when agents produce large PDFs, images, or binaries.  Downstream
    activities (``execute_ceremony``) read content from disk as needed.
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    workspaces_root = "/workspaces"
    workspace_dir = os.path.join(workspaces_root, workspace_id)

    collected = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for task_id, role in member_task_outputs.items():
            role_data: Dict[str, Any] = {
                "task_id": task_id,
                "workspace_path": "",
                "files": [],
                "api_output_count": 0,
            }

            # Count API outputs (don't transfer them)
            try:
                resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}/outputs")
                if resp.status_code == 200:
                    role_data["api_output_count"] = len(resp.json())
            except Exception as e:
                logger.warning(f"⚠️ Could not collect API output count from {task_id}: {e}")

            # Build file manifest (names + sizes only)
            try:
                task_resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
                if task_resp.status_code == 200:
                    task_data = task_resp.json()
                    task_workspace = task_data.get("workspace_id", "")
                    ws_path = os.path.join(workspaces_root, task_workspace) if task_workspace else workspace_dir
                    role_data["workspace_path"] = ws_path
                    if os.path.isdir(ws_path):
                        for root, dirs, filenames in os.walk(ws_path):
                            for fname in filenames:
                                fpath = os.path.join(root, fname)
                                rel = os.path.relpath(fpath, ws_path)
                                try:
                                    sz = os.path.getsize(fpath)
                                except OSError:
                                    sz = 0
                                role_data["files"].append({"filename": rel, "size": sz})
            except Exception as e:
                logger.warning(f"⚠️ Could not scan workspace for {task_id}: {e}")

            collected[role] = role_data

    total_files = sum(len(r.get("files", [])) for r in collected.values())
    logger.info(
        f"📦 COLLECTED MANIFEST | TF: {task_force_id} | "
        f"Roles: {list(collected.keys())} | Total files: {total_files}"
    )
    return collected


@activity.defn
async def execute_ceremony(
    task_force_id: str,
    ceremony_id: int,
    ceremony_data: Dict[str, Any],
    member_task_outputs: Dict[str, str],
    workspace_id: str = "",
    collected_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a ceremony with type-specific behavior.

    - planning:     Generate a work plan via LLM, write CEREMONY_PLAN.md
    - peer_review:  Collect developer outputs, write REVIEW_BRIEF.md for reviewer
    - aggregation:  Collect all outputs, produce FINAL_SUMMARY.md
    - sync/custom:  Write coordination notes as SYNC_NOTES.md
    """
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
    ceremony_type = ceremony_data.get("ceremony_type", "custom")
    ceremony_name = ceremony_data.get("name", "Unnamed Ceremony")

    logger.info(
        f"🎭 CEREMONY | TF: {task_force_id} | #{ceremony_id} | "
        f"Type: {ceremony_type} | Mode: {ceremony_data.get('mode')}"
    )

    # Resolve workspace_id if not provided
    if not workspace_id:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{control_plane_url}/api/task-forces/{task_force_id}")
                if resp.status_code == 200:
                    workspace_id = resp.json().get("workspace_id", "")
        except Exception:
            pass

    workspaces_root = "/workspaces"
    workspace_dir = os.path.join(workspaces_root, workspace_id) if workspace_id else ""

    # Build role summary from collected data or member_task_outputs
    role_summary_lines = []
    if collected_data:
        for role, data in collected_data.items():
            if isinstance(data, dict) and "files" in data:
                files = [f.get("filename", "?") for f in data.get("files", [])]
                n_outputs = data.get("api_output_count", 0)
                role_summary_lines.append(
                    f"- **{role}**: {n_outputs} iterations, files: {', '.join(files) or 'none'}"
                )
            elif isinstance(data, str):
                # Simple string value (e.g. plan text) — skip for role summary
                continue
            else:
                role_summary_lines.append(f"- **{role}**: (data collected)")
    if not role_summary_lines:
        for task_id, role in member_task_outputs.items():
            role_summary_lines.append(f"- **{role}**: task {task_id}")

    role_summary = "\n".join(role_summary_lines)
    artifact_content = ""
    artifact_filename = ""

    # ── Type-specific ceremony execution ──

    if ceremony_type == "planning":
        # Planning ceremony: write plan BEFORE agents start
        artifact_filename = "CEREMONY_PLAN.md"
        artifact_content = (
            f"# 📋 Ceremony Plan: {ceremony_name}\n\n"
            f"**Task Force:** {task_force_id}\n"
            f"**Ceremony Type:** Planning\n"
            f"**Generated at:** {__import__('datetime').datetime.utcnow().isoformat()}\n\n"
            f"## Team\n{role_summary}\n\n"
        )
        # If we have LLM-generated plan data, include it
        if collected_data and collected_data.get("plan"):
            artifact_content += f"## Detailed Plan\n\n{collected_data['plan']}\n"
        else:
            artifact_content += (
                "## Instructions\n\n"
                "Each team member should:\n"
                "1. Read this plan and coordinate accordingly\n"
                "2. Focus on your assigned role responsibilities\n"
                "3. Place all deliverables in the shared workspace\n"
                "4. Write a DONE_<YOUR_ROLE>.md file when finished\n"
                "5. Only the designated lead should request deployments\n"
            )

    elif ceremony_type == "peer_review":
        # Peer review: collect dev outputs, write review brief for reviewer
        artifact_filename = "REVIEW_BRIEF.md"
        artifact_content = (
            f"# 🔍 Review Brief: {ceremony_name}\n\n"
            f"**Task Force:** {task_force_id}\n"
            f"**Ceremony Type:** Peer Review\n"
            f"**Generated at:** {__import__('datetime').datetime.utcnow().isoformat()}\n\n"
            f"## Deliverables to Review\n\n{role_summary}\n\n"
        )
        # Include file contents by reading from disk (not from Temporal payload)
        if collected_data:
            artifact_content += "## File Contents\n\n"
            TEXT_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml",
                         ".toml", ".cfg", ".ini", ".csv", ".html", ".css",
                         ".js", ".ts", ".sh", ".bat", ".xml", ".rst", ".log"}
            MAX_PREVIEW_CHARS = 2000
            for role, data in collected_data.items():
                artifact_content += f"### {role} Deliverables\n\n"
                ws_path = data.get("workspace_path", "") if isinstance(data, dict) else ""
                for finfo in (data.get("files", []) if isinstance(data, dict) else []):
                    fname = finfo.get("filename", "?")
                    fsize = finfo.get("size", 0)
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in TEXT_EXTS or fname in ("result.json",):
                        artifact_content += f"- `{fname}` ({fsize} bytes) — *binary/non-text*\n"
                        continue
                    # Read preview from disk
                    fpath = os.path.join(ws_path, fname) if ws_path else ""
                    preview = ""
                    if fpath and os.path.isfile(fpath):
                        try:
                            with open(fpath, "r", errors="replace") as fp:
                                preview = fp.read(MAX_PREVIEW_CHARS)
                        except Exception:
                            preview = "(could not read)"
                    if preview:
                        artifact_content += f"#### `{fname}` ({fsize} bytes)\n```\n{preview}\n```\n\n"
                    else:
                        artifact_content += f"- `{fname}` ({fsize} bytes)\n"

            artifact_content += (
                "## Review Instructions\n\n"
                "1. Review each deliverable above for correctness and completeness\n"
                "2. Check that the implementation matches the objective\n"
                "3. Note any issues, bugs, or improvements needed\n"
                "4. Write your review findings to REVIEW_FINDINGS.md\n"
                "5. If the code is acceptable, confirm in your findings\n"
            )

    elif ceremony_type == "aggregation":
        # Aggregation: collect everything and produce final summary
        artifact_filename = "FINAL_SUMMARY.md"
        artifact_content = (
            f"# 📊 Final Summary: {ceremony_name}\n\n"
            f"**Task Force:** {task_force_id}\n"
            f"**Ceremony Type:** Aggregation\n"
            f"**Generated at:** {__import__('datetime').datetime.utcnow().isoformat()}\n\n"
            f"## All Team Outputs\n\n{role_summary}\n\n"
        )
        if collected_data:
            artifact_content += "## Deliverable Details\n\n"
            for role, data in collected_data.items():
                artifact_content += f"### {role}\n"
                for finfo in (data.get("files", []) if isinstance(data, dict) else []):
                    artifact_content += f"- `{finfo.get('filename', '?')}` ({finfo.get('size', 0)} bytes)\n"
                artifact_content += "\n"

    else:
        # sync / custom: write coordination notes
        artifact_filename = f"SYNC_NOTES_{ceremony_id}.md"
        artifact_content = (
            f"# 🔄 Sync Notes: {ceremony_name}\n\n"
            f"**Task Force:** {task_force_id}\n"
            f"**Ceremony Type:** {ceremony_type}\n"
            f"**Generated at:** {__import__('datetime').datetime.utcnow().isoformat()}\n\n"
            f"## Participants\n\n{role_summary}\n\n"
            f"## Status\n\nAll participants have been synchronized.\n"
        )

    # Write the artifact to workspace
    if artifact_content and workspace_dir:
        try:
            os.makedirs(workspace_dir, exist_ok=True)
            filepath = os.path.join(workspace_dir, artifact_filename)
            with open(filepath, "w") as f:
                f.write(artifact_content)
            os.chmod(filepath, 0o666)
            logger.info(f"📝 CEREMONY ARTIFACT | {artifact_filename} → {filepath}")
        except Exception as e:
            logger.warning(f"⚠️ Could not write ceremony artifact: {e}")

    # Also store in ceremony state API for traceability
    api_id = None
    if artifact_content and task_force_id:
        kind_map = {
            "planning": "plan",
            "peer_review": "review_brief",
            "aggregation": "summary",
        }
        artifact_kind = kind_map.get(ceremony_type, "custom")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{control_plane_url}/api/task-forces/{task_force_id}/artifacts",
                    json={
                        "kind": artifact_kind,
                        "ceremony_id": ceremony_id or None,
                        "filename": artifact_filename,
                        "title": ceremony_name,
                        "content": artifact_content[:64000],
                        "rework_cycle": 0,
                    },
                )
                if resp.status_code in (200, 201):
                    api_id = resp.json().get("id")
                    logger.info(f"📝 Ceremony artifact stored in API: #{api_id}")
                else:
                    logger.warning(f"⚠️ Ceremony artifact API store failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Could not store ceremony artifact via API: {e}")

    summary = (
        f"Ceremony '{ceremony_name}' ({ceremony_type}) completed.\n"
        f"Participants: {len(member_task_outputs)} agents\n"
        f"Artifact: {artifact_filename}\n"
        f"Roles: {list(member_task_outputs.values())}\n"
    )

    return {
        "ceremony_id": ceremony_id,
        "ceremony_type": ceremony_type,
        "status": "completed",
        "summary": summary,
        "artifact_filename": artifact_filename,
        "collected_outputs": {
            k: (
                f"{len(v.get('files', []))} files, {v.get('api_output_count', 0)} iterations"
                if isinstance(v, dict) and "files" in v
                else str(v)[:100]
            )
            for k, v in (collected_data or {}).items()
        },
    }


@activity.defn
async def finalize_task_force(task_force_id: str, status: str = "completed") -> Dict[str, Any]:
    """Mark the Task Force as completed/failed."""
    import httpx
    control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")

    endpoint = "complete" if status == "completed" else "cancel"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Update task force status directly via the router
        # (We POST to a status endpoint)
        try:
            resp = await client.post(
                f"{control_plane_url}/api/task-forces/{task_force_id}/{endpoint}"
            )
            if resp.status_code == 200:
                logger.info(f"✅ Task Force {task_force_id} finalized as {status}")
                return {"status": status}
        except Exception as e:
            logger.warning(f"⚠️ Could not finalize task force: {e}")

    return {"status": status, "task_force_id": task_force_id}


# =============================================================================
# Task Force Workflow
# =============================================================================

@workflow.defn
class TaskForceWorkflow:
    """Orchestrates a multi-agent Task Force.

    Execution flow:
    1. Load Task Force definition (members + ceremonies)
    2. Group members by ``execution_order`` (lower = earlier)
    3. For each order-group:
       a. Start member tasks in this group (as child AgentTaskWorkflow instances)
       b. **Await** all members in this group to reach a terminal state
       c. Only then proceed to the next order-group
    4. If ceremonies are defined they are woven between the order-groups –
       e.g. a *planning* ceremony runs before any agents start, a
       *peer_review* ceremony runs between the two halves, etc.

    Members share a workspace, allowing context sharing between agents.
    The ``execution_order`` field on each member is the primary sequencing
    mechanism.  Members with the *same* ``execution_order`` run in parallel;
    the workflow blocks on each group finishing before launching the next.
    """

    @workflow.run
    async def run(self, task_force_id: str) -> Dict[str, Any]:
        logger.info(f"🎯 TaskForceWorkflow | Starting for {task_force_id}")

        # 1. Load the Task Force definition
        tf_data = await workflow.execute_activity(
            load_task_force,
            args=[task_force_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        members = tf_data.get("members", [])
        ceremonies = sorted(
            tf_data.get("ceremonies", []),
            key=lambda c: c.get("sequence_order", 0),
        )
        tf_name = tf_data.get("name", task_force_id)

        if not members:
            return {"status": "failed", "error": "No members in Task Force"}

        # Post initial progress
        member_roles = [m.get("role", "Agent") for m in members]
        await workflow.execute_activity(
            post_task_force_progress,
            args=[
                task_force_id,
                f"🚀 **Task Force '{tf_name}' started**\n\n"
                f"**Team:** {len(members)} agents\n"
                f"**Roles:** {', '.join(member_roles)}\n"
                f"**Ceremonies:** {len(ceremonies)}\n\n"
                f"Launching member agents now..."
            ],
            start_to_close_timeout=timedelta(seconds=15),
        )

        workspace_id = tf_data.get("workspace_id", "")

        # 2. Run the workflow based on ceremonies
        if not ceremonies:
            result = await self._run_all_parallel(task_force_id, members, tf_name)
        else:
            result = await self._run_with_ceremonies(
                task_force_id, members, ceremonies, tf_name, workspace_id
            )

        # 3. Finalize
        final_status = "completed" if not result.get("error") else "failed"

        # Post completion summary
        member_results = result.get("members", {})
        summary_lines = []
        for tid, fstatus in member_results.items():
            icon = "✅" if fstatus == "completed" else "❌"
            summary_lines.append(f"  {icon} {tid}: {fstatus}")

        await workflow.execute_activity(
            post_task_force_progress,
            args=[
                task_force_id,
                f"{'✅' if final_status == 'completed' else '❌'} **Task Force '{tf_name}' {final_status}**\n\n"
                f"**Results:**\n" + "\n".join(summary_lines or ["No member results recorded."])
            ],
            start_to_close_timeout=timedelta(seconds=15),
        )

        await workflow.execute_activity(
            finalize_task_force,
            args=[task_force_id, final_status],
            start_to_close_timeout=timedelta(minutes=2),
        )

        # Update coordinator task status
        await workflow.execute_activity(
            update_coordinator_task_status,
            args=[task_force_id, final_status],
            start_to_close_timeout=timedelta(seconds=15),
        )

        return result

    async def _run_all_parallel(
        self, task_force_id: str, members: list, tf_name: str = ""
    ) -> Dict[str, Any]:
        """Run members grouped by execution_order, awaiting each group before
        starting the next.  Uses native Temporal child workflows instead of
        independent peer workflows + HTTP polling."""

        order_groups = self._group_by_execution_order(members)
        all_member_tasks: Dict[str, str] = {}       # task_id → role
        all_child_handles: Dict[str, Any] = {}       # task_id → ChildWorkflowHandle

        for order_val, group in order_groups:
            next_idx = 0
            # Find the index of this group in order_groups
            for i, (ov, _) in enumerate(order_groups):
                if ov == order_val:
                    next_idx = i
                    break

            await self._start_next_order_group(
                task_force_id, order_groups, next_idx,
                all_member_tasks, all_child_handles,
            )

            # Await the children from this group only
            group_task_ids = [
                m.get("task_id") for m in group
                if m.get("task_id") and m.get("task_id") in all_child_handles
            ]
            if group_task_ids:
                await self._await_children(
                    task_force_id, group_task_ids,
                    all_child_handles, all_member_tasks,
                    phase_name=f"Execution group {order_val}",
                )

        # Derive final statuses from child results (already completed)
        final_members = {}
        for task_id, handle in all_child_handles.items():
            try:
                result = handle._result if hasattr(handle, '_result') else {}
                final_members[task_id] = result.get("status", "completed")
            except Exception:
                final_members[task_id] = "unknown"

        return {"status": "completed", "members": final_members}

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _group_by_execution_order(members: list):
        """Return a sorted list of (order_value, [members]) tuples."""
        from itertools import groupby
        sorted_members = sorted(members, key=lambda m: m.get("execution_order", 0))
        return [
            (k, list(g))
            for k, g in groupby(sorted_members, key=lambda m: m.get("execution_order", 0))
        ]

    async def _start_next_order_group(
        self,
        task_force_id: str,
        order_groups: list,
        group_idx: int,
        all_member_tasks: Dict[str, str],
        all_child_handles: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Start every member in ``order_groups[group_idx]`` as Temporal **child
        workflows**, register them in *all_member_tasks* and
        *all_child_handles*, then return the incremented index.

        Each agent gets a child ``AgentTaskWorkflow`` — the parent can later
        ``await handle.result()`` without polling.  A lightweight activity
        persists the ``workflow_id`` back to the control-plane DB so the UI
        and capability-approval signals keep working.
        """
        if all_child_handles is None:
            all_child_handles = {}

        if group_idx >= len(order_groups):
            return group_idx

        order_val, group = order_groups[group_idx]
        group_roles = [m.get("role", "Agent") for m in group]

        await workflow.execute_activity(
            post_task_force_progress,
            args=[
                task_force_id,
                f"🚀 **Starting execution group {order_val}** "
                f"({len(group)} agent(s): {', '.join(group_roles)})"
            ],
            start_to_close_timeout=timedelta(seconds=15),
        )

        for member in group:
            task_id = member.get("task_id")
            if not task_id or task_id in all_member_tasks:
                continue

            llm_model = member.get("llm_model") or "gemini-flash-latest"
            base_image_key = member.get("base_image") or "openclaw"
            base_image = f"localhost:5000/openclaw-agent:{base_image_key}"

            # Fetch the task's current_image from DB — it may have been
            # patched to an inherited image from a previous rework cycle.
            db_image = await workflow.execute_activity(
                get_task_current_image,
                args=[task_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            if db_image and db_image != base_image:
                workflow.logger.info(
                    f"♻️  Using DB image for {task_id}: {db_image} "
                    f"(instead of base {base_image})"
                )
                base_image = db_image

            child_wf_id = f"task-workflow-{task_id}"

            # Launch as a Temporal child workflow (native parent-child)
            handle = await workflow.start_child_workflow(
                "AgentTaskWorkflow",
                args=[task_id, llm_model, base_image],
                id=child_wf_id,
            )
            all_child_handles[task_id] = handle
            all_member_tasks[task_id] = member.get("role", "Agent")

            # Persist workflow_id to control-plane (fire-and-forget)
            try:
                await workflow.execute_activity(
                    persist_member_workflow_id,
                    args=[task_id, child_wf_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
            except Exception:
                pass  # non-critical

        logger.info(
            f"Order-group {order_val} started as child workflows: {group_roles} "
            f"(total active: {len(all_member_tasks)})"
        )
        return group_idx + 1

    async def _await_children(
        self,
        task_force_id: str,
        task_ids: list,
        all_child_handles: Dict[str, Any],
        all_member_tasks: Dict[str, str],
        phase_name: str = "Awaiting",
    ) -> Dict[str, Dict[str, Any]]:
        """Await a set of child-workflow handles natively.

        Uses ``asyncio.gather`` on the Temporal child handles — **no HTTP
        polling, no sleep loops**.  Each child's structured result is
        returned keyed by ``task_id``.

        Posts a progress update before waiting and after completion.
        """
        handles_to_wait = {
            tid: all_child_handles[tid]
            for tid in task_ids
            if tid in all_child_handles
        }
        if not handles_to_wait:
            return {}

        roles = [all_member_tasks.get(tid, "Agent") for tid in handles_to_wait]
        await workflow.execute_activity(
            post_task_force_progress,
            args=[
                task_force_id,
                f"⏳ **{phase_name}** — waiting for {len(handles_to_wait)} agent(s):\n"
                + ", ".join(roles)
            ],
            start_to_close_timeout=timedelta(seconds=15),
        )

        async def _await_one(tid: str, handle):
            try:
                result = await handle            # ChildWorkflowHandle is a Task — just await it
                return tid, result
            except Exception as e:
                logger.error(f"Child workflow {tid} failed: {e}")
                return tid, {"task_id": tid, "status": "failed", "error": str(e)}

        completed = await asyncio.gather(
            *[_await_one(tid, h) for tid, h in handles_to_wait.items()]
        )
        results = dict(completed)

        # Post completion summary
        status_lines = []
        for tid, res in results.items():
            s = res.get("status", "unknown")
            icon = "✅" if s == "completed" else "❌"
            role = all_member_tasks.get(tid, "Agent")
            status_lines.append(f"  {icon} **{role}**: {s}")

        await workflow.execute_activity(
            post_task_force_progress,
            args=[
                task_force_id,
                f"📊 **{phase_name}** — all done:\n" + "\n".join(status_lines)
            ],
            start_to_close_timeout=timedelta(seconds=15),
        )

        return results

    async def _run_with_ceremonies(
        self,
        task_force_id: str,
        members: list,
        ceremonies: list,
        tf_name: str = "",
        workspace_id: str = "",
    ) -> Dict[str, Any]:
        """Run members with ceremony-driven phased execution.

        Properly sequences work based on ``execution_order`` AND ceremony types:
        - planning:     Generate plan → write to workspace → start first order-group
        - peer_review:  Wait for all started groups → collect outputs → write
                        review brief → start next order-group(s)
        - review_gate:  Wait for all → read verdict file → if FAIL, create new
                        tasks for target order and re-run; loop until PASS or
                        max_rework_cycles exceeded
        - aggregation:  Wait for all → collect → produce summary
        - sync/custom:  Checkpoint between phases

        Members are grouped by ``execution_order``.  Between ceremonies the
        workflow starts the next unstarted order-group, waits for it, then
        proceeds to the next ceremony.  This guarantees that members with a
        higher ``execution_order`` never begin before lower-order members have
        finished.
        """
        results = {}
        member_map = {m.get("id"): m for m in members}

        # Build ordered groups — each element is (order_val, [member, ...])
        order_groups = self._group_by_execution_order(members)
        # Track which order-groups have been started / completed
        next_group_idx = 0          # index into order_groups not yet started
        all_member_tasks: Dict[str, str] = {}  # task_id → role (accumulated)
        all_child_handles: Dict[str, Any] = {}  # task_id → ChildWorkflowHandle

        # Build team info string for LLM
        team_info = "\n".join([
            f"- {m.get('role', 'Agent')}: {m.get('responsibilities', 'N/A')} (task: {m.get('task_id', '?')})"
            for m in members
        ])

        # If the first ceremony is NOT planning, we must start the first
        # order-group now — otherwise nobody kicks off the agents.
        first_ceremony_type = ceremonies[0].get("ceremony_type", "") if ceremonies else ""
        if first_ceremony_type != "planning" and order_groups:
            logger.info(
                f"🚀 No planning ceremony — auto-starting first order-group "
                f"for {task_force_id}"
            )
            next_group_idx = await self._start_next_order_group(
                task_force_id, order_groups, next_group_idx,
                all_member_tasks, all_child_handles,
            )

        for ceremony_idx, ceremony in enumerate(ceremonies):
            ceremony_name = ceremony.get("name", "Unnamed")
            ceremony_type = ceremony.get("ceremony_type", "custom")
            participant_ids = ceremony.get("participant_member_ids")
            trigger = ceremony.get("trigger_condition", "after_all_complete")

            logger.info(
                f"🎭 Ceremony phase [{ceremony_idx + 1}/{len(ceremonies)}]: "
                f"{ceremony_name} ({ceremony_type}) | "
                f"Trigger: {trigger} | Participants: {participant_ids or 'all'}"
            )

            await workflow.execute_activity(
                post_task_force_progress,
                args=[
                    task_force_id,
                    f"🎭 **Ceremony Phase: {ceremony_name}** ({ceremony_type})\n\n"
                    f"Phase {ceremony_idx + 1} of {len(ceremonies)} beginning..."
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )

            # ── PLANNING CEREMONY ──
            if ceremony_type == "planning":
                # 1. Generate plan via LLM BEFORE starting agents
                plan_result = await workflow.execute_activity(
                    generate_ceremony_plan,
                    args=[
                        ceremony.get("description", tf_name),
                        team_info,
                        f"Ceremony: {ceremony_name}\nType: Planning\nDescription: {ceremony.get('description', '')}",
                        workspace_id,
                    ],
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # 2. Write plan to workspace + API
                plan_text = plan_result.get("plan", "No plan generated.")
                await workflow.execute_activity(
                    write_ceremony_artifact,
                    args=[
                        workspace_id, "CEREMONY_PLAN.md", plan_text,
                        task_force_id, ceremony.get("id", 0), "plan",
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                )

                # 3. Execute ceremony (writes the formal planning artifact)
                plan_tasks = {m.get("task_id"): m.get("role", "Agent") for m in members if m.get("task_id")}
                ceremony_result = await workflow.execute_activity(
                    execute_ceremony,
                    args=[
                        task_force_id, ceremony.get("id"), ceremony,
                        plan_tasks, workspace_id,
                        {"plan": plan_text},
                    ],
                    start_to_close_timeout=timedelta(minutes=5),
                )
                results[ceremony_name] = ceremony_result

                await workflow.execute_activity(
                    post_task_force_progress,
                    args=[
                        task_force_id,
                        f"📋 **Planning ceremony complete**\n\n"
                        f"Work plan generated and written to `CEREMONY_PLAN.md`.\n"
                        f"Plan status: {plan_result.get('status', 'unknown')}\n\n"
                        f"Starting team members..."
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                # 4. Start the FIRST order-group only (the rest will be
                #    launched after each group completes)
                next_group_idx = await self._start_next_order_group(
                    task_force_id, order_groups, next_group_idx,
                    all_member_tasks, all_child_handles,
                )

            # ── PEER REVIEW CEREMONY ──
            elif ceremony_type == "peer_review":
                # 1. Wait for every already-started group to finish (native await)
                if all_member_tasks:
                    started_ids = list(all_member_tasks.keys())
                    await self._await_children(
                        task_force_id, started_ids,
                        all_child_handles, all_member_tasks,
                        phase_name=f"Peer Review: {ceremony_name}",
                    )

                # 2. Collect outputs from completed members
                collected = await workflow.execute_activity(
                    collect_member_outputs,
                    args=[task_force_id, all_member_tasks, workspace_id],
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # 3. Write review brief to workspace
                ceremony_result = await workflow.execute_activity(
                    execute_ceremony,
                    args=[
                        task_force_id, ceremony.get("id"), ceremony,
                        all_member_tasks, workspace_id, collected,
                    ],
                    start_to_close_timeout=timedelta(minutes=5),
                )
                results[ceremony_name] = ceremony_result

                # 4. Start the NEXT order-group (reviewers / testers)
                prev_tasks = set(all_member_tasks.keys())
                if next_group_idx < len(order_groups):
                    next_group_idx = await self._start_next_order_group(
                        task_force_id, order_groups, next_group_idx,
                        all_member_tasks, all_child_handles,
                    )

                newly_started = {
                    tid: role for tid, role in all_member_tasks.items()
                    if tid not in prev_tasks
                }
                if newly_started:
                    await workflow.execute_activity(
                        post_task_force_progress,
                        args=[
                            task_force_id,
                            f"🔍 **Peer Review phase started**\n\n"
                            f"Development outputs collected and written to `REVIEW_BRIEF.md`.\n"
                            f"Started next group: {', '.join(newly_started.values())}\n\n"
                            f"Files available for review:\n"
                            + "\n".join([
                                f"- `{f.get('filename', '?')}` ({f.get('size', 0)} bytes)"
                                for role_data in collected.values()
                                for f in role_data.get("files", [])
                            ][:10])
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

            # ── AGGREGATION CEREMONY ──
            elif ceremony_type == "aggregation":
                # Wait for ALL active tasks to complete (native await)
                if all_member_tasks:
                    started_ids = list(all_member_tasks.keys())
                    await self._await_children(
                        task_force_id, started_ids,
                        all_child_handles, all_member_tasks,
                        phase_name=f"Aggregation: {ceremony_name}",
                    )

                # Collect all outputs
                collected = await workflow.execute_activity(
                    collect_member_outputs,
                    args=[task_force_id, all_member_tasks, workspace_id],
                    start_to_close_timeout=timedelta(minutes=2),
                )

                ceremony_result = await workflow.execute_activity(
                    execute_ceremony,
                    args=[
                        task_force_id, ceremony.get("id"), ceremony,
                        all_member_tasks, workspace_id, collected,
                    ],
                    start_to_close_timeout=timedelta(minutes=5),
                )
                results[ceremony_name] = ceremony_result

                await workflow.execute_activity(
                    post_task_force_progress,
                    args=[
                        task_force_id,
                        f"📊 **Aggregation ceremony complete**\n\n"
                        f"All outputs collected and summarized in `FINAL_SUMMARY.md`.\n"
                        f"Participants: {', '.join(all_member_tasks.values())}"
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

                # Start the NEXT order-group (just like peer_review does)
                if next_group_idx < len(order_groups):
                    next_group_idx = await self._start_next_order_group(
                        task_force_id, order_groups, next_group_idx,
                        all_member_tasks, all_child_handles,
                    )

            # ── REVIEW GATE CEREMONY ──
            elif ceremony_type == "review_gate":
                target_order = ceremony.get("review_target_order") or 0
                max_cycles = ceremony.get("max_rework_cycles") or 2
                verdict_file = ceremony.get("verdict_file") or "REVIEW_BRIEF.md"
                rework_cycle = 0

                while True:
                    # 1. Wait for every started task (native await)
                    if all_member_tasks:
                        started_ids = list(all_member_tasks.keys())
                        await self._await_children(
                            task_force_id, started_ids,
                            all_child_handles, all_member_tasks,
                            phase_name=f"Review Gate: {ceremony_name}",
                        )

                    # 2. Read the verdict — API first, then file fallback
                    verdict_result = await workflow.execute_activity(
                        read_verdict_file,
                        args=[workspace_id, verdict_file, task_force_id, rework_cycle],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    verdict = verdict_result.get("verdict", "unknown")
                    verdict_content = verdict_result.get("content", "")
                    verdict_source = verdict_result.get("source", "file")

                    await workflow.execute_activity(
                        post_task_force_progress,
                        args=[
                            task_force_id,
                            f"🔍 **Review Gate verdict: {verdict.upper()}**\n\n"
                            f"Source: {verdict_source} | File: `{verdict_file}` | Cycle: {rework_cycle}/{max_cycles}"
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    # Post state exchange so all agents can see the verdict decision
                    try:
                        await workflow.execute_activity(
                            post_agent_state_exchange,
                            args=[
                                task_force_id,
                                "system",  # from system / ceremony
                                "decision",
                                f"Review Gate: {verdict.upper()}",
                                f"Verdict: {verdict.upper()} (cycle {rework_cycle}, source: {verdict_source})",
                            ],
                            start_to_close_timeout=timedelta(seconds=10),
                        )
                    except Exception:
                        pass  # non-critical

                    # 3. If PASS → proceed; unknown/missing → FAIL (fail-safe)
                    if verdict == "pass":
                        logger.info(
                            f"✅ Review gate PASSED (verdict={verdict}, cycle={rework_cycle})"
                        )
                        ceremony_result = {
                            "verdict": verdict, "cycle": rework_cycle,
                            "action": "proceed", "source": verdict_source,
                        }
                        results[ceremony_name] = ceremony_result

                        # Start the NEXT order-group (just like peer_review does)
                        if next_group_idx < len(order_groups):
                            next_group_idx = await self._start_next_order_group(
                                task_force_id, order_groups, next_group_idx,
                                all_member_tasks, all_child_handles,
                            )
                        break

                    # Treat "unknown" (missing verdict) as FAIL — fail-safe
                    if verdict == "unknown":
                        logger.warning(
                            f"⚠️ Review gate: no verdict found (source={verdict_source}, cycle={rework_cycle}) — treating as FAIL"
                        )
                        verdict = "fail"
                        verdict_content = (
                            "No verdict file or API verdict was found. "
                            "The reviewing agent may not have written REVIEW_VERDICT.md. "
                            "Please ensure you write the verdict file before completing your task."
                        )

                    # 4. FAIL — check cycle limit
                    rework_cycle += 1
                    if max_cycles > 0 and rework_cycle > max_cycles:
                        logger.warning(
                            f"⚠️ Review gate: max rework cycles ({max_cycles}) exceeded"
                        )
                        await workflow.execute_activity(
                            post_task_force_progress,
                            args=[
                                task_force_id,
                                f"⚠️ **Review Gate: Max rework cycles reached ({max_cycles})**\n\n"
                                f"Proceeding despite FAIL verdict."
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        ceremony_result = {"verdict": "fail", "cycle": rework_cycle - 1, "action": "max_cycles_exceeded"}
                        results[ceremony_name] = ceremony_result
                        break

                    # 5. FAIL within limits → create rework tasks
                    await workflow.execute_activity(
                        post_task_force_progress,
                        args=[
                            task_force_id,
                            f"🔄 **Review Gate: REWORK CYCLE {rework_cycle}**\n\n"
                            f"Reviewer requested changes. Re-running from execution order {target_order}.\n"
                            f"Creating fresh tasks for affected members..."
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    rework_result = await workflow.execute_activity(
                        create_rework_tasks,
                        args=[
                            task_force_id, members, target_order,
                            workspace_id, verdict_content, rework_cycle,
                        ],
                        start_to_close_timeout=timedelta(minutes=5),
                    )

                    # 6. Update members list and rebuild order groups
                    new_members = rework_result.get("new_members", [])
                    new_task_ids = {m["task_id"] for m in new_members}
                    new_member_ids = {m.get("id") for m in new_members}
                    members = [
                        m for m in members if m.get("id") not in new_member_ids
                    ] + new_members

                    # Rebuild order groups and reset cursor to target
                    order_groups = self._group_by_execution_order(members)
                    next_group_idx = 0
                    for idx, (ov, _) in enumerate(order_groups):
                        if ov >= target_order:
                            next_group_idx = idx
                            break

                    # Clear rework members from tracking so they start fresh
                    all_member_tasks = {
                        tid: role for tid, role in all_member_tasks.items()
                        if tid not in new_task_ids
                    }
                    all_child_handles = {
                        tid: h for tid, h in all_child_handles.items()
                        if tid not in new_task_ids
                    }

                    # 7. Re-run from target order as child workflows
                    while next_group_idx < len(order_groups):
                        next_group_idx = await self._start_next_order_group(
                            task_force_id, order_groups, next_group_idx,
                            all_member_tasks, all_child_handles,
                        )
                        # Await each rework group before starting the next
                        rework_ids = [
                            tid for tid in all_member_tasks
                            if tid in new_task_ids and tid in all_child_handles
                        ]
                        if rework_ids:
                            await self._await_children(
                                task_force_id, rework_ids,
                                all_child_handles, all_member_tasks,
                                phase_name=f"Rework cycle {rework_cycle}",
                            )

                    # Loop back to read verdict again

            # ── SYNC / CUSTOM CEREMONY ──
            else:
                # Determine participants
                if participant_ids:
                    participants = [
                        member_map[mid] for mid in participant_ids
                        if mid in member_map
                    ]
                else:
                    participants = members

                # Start the next order-group(s) whose members overlap with
                # the ceremony participants.  This keeps order-group
                # sequencing intact while honouring explicit participant lists.
                participant_task_ids = {
                    m.get("task_id") for m in participants if m.get("task_id")
                }
                while next_group_idx < len(order_groups):
                    _, grp = order_groups[next_group_idx]
                    grp_task_ids = {m.get("task_id") for m in grp if m.get("task_id")}
                    if grp_task_ids & participant_task_ids:
                        next_group_idx = await self._start_next_order_group(
                            task_force_id, order_groups, next_group_idx,
                            all_member_tasks, all_child_handles,
                        )
                    else:
                        break

                # Wait for this phase's participants (native await)
                phase_ids = [
                    tid for tid in all_member_tasks
                    if tid in participant_task_ids and tid in all_child_handles
                ]
                if phase_ids:
                    await self._await_children(
                        task_force_id, phase_ids,
                        all_child_handles, all_member_tasks,
                        phase_name=ceremony_name,
                    )

                phase_tasks = {
                    tid: role for tid, role in all_member_tasks.items()
                    if tid in participant_task_ids
                }
                ceremony_result = await workflow.execute_activity(
                    execute_ceremony,
                    args=[
                        task_force_id, ceremony.get("id"), ceremony,
                        phase_tasks, workspace_id, None,
                    ],
                    start_to_close_timeout=timedelta(minutes=5),
                )
                results[ceremony_name] = ceremony_result

        # ── Final: start & await any remaining order-groups ──
        while next_group_idx < len(order_groups):
            next_group_idx = await self._start_next_order_group(
                task_force_id, order_groups, next_group_idx,
                all_member_tasks, all_child_handles,
            )

        # Await every outstanding child workflow
        remaining_ids = [
            tid for tid in all_member_tasks if tid in all_child_handles
        ]
        child_results = {}
        if remaining_ids:
            child_results = await self._await_children(
                task_force_id, remaining_ids,
                all_child_handles, all_member_tasks,
                phase_name="Final wait",
            )

        # Derive final member statuses from child workflow results
        final_members = {}
        for task_id in all_member_tasks:
            if task_id in child_results:
                final_members[task_id] = child_results[task_id].get("status", "completed")
            else:
                final_members[task_id] = "completed"

        return {"status": "completed", "ceremonies": results, "members": final_members}

    async def _wait_for_tasks(
        self,
        task_force_id: str,
        tasks: Dict[str, str],
        phase_name: str,
        timeout_minutes: int = 60,
    ) -> None:
        """Poll tasks until all complete or timeout. Handles paused tasks."""
        max_polls = (timeout_minutes * 60) // 30
        paused_notified: dict = {}
        last_progress_poll = -1

        for poll in range(max_polls):
            all_done = True
            status_lines = []
            completed_count = 0

            for task_id, role in tasks.items():
                result = await workflow.execute_activity(
                    poll_member_task_status,
                    args=[task_id],
                    start_to_close_timeout=timedelta(seconds=30),
                )
                member_status = result.get("status", "unknown")

                if member_status in ("completed", "failed"):
                    completed_count += 1
                    icon = "✅" if member_status == "completed" else "❌"
                    status_lines.append(f"  {icon} **{role}**: {member_status}")
                    continue

                all_done = False

                if member_status == "paused" and not paused_notified.get(task_id):
                    paused_notified[task_id] = True
                    await workflow.execute_activity(
                        post_task_force_progress,
                        args=[
                            task_force_id,
                            f"🔒 **{role}** is requesting a new capability during "
                            f"'{phase_name}'.\nApprove it on the **Approvals** page."
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    status_lines.append(f"  🔒 **{role}**: waiting for approval")
                elif member_status == "running" and paused_notified.get(task_id):
                    paused_notified[task_id] = False
                    status_lines.append(f"  ⏳ **{role}**: running")
                else:
                    status_lines.append(f"  ⏳ **{role}**: {member_status}")

            # Post progress every 2 minutes
            if status_lines and (poll - last_progress_poll >= 4 or all_done):
                last_progress_poll = poll
                await workflow.execute_activity(
                    post_task_force_progress,
                    args=[
                        task_force_id,
                        f"📊 **{phase_name}** ({completed_count}/{len(tasks)} done)\n\n"
                        + "\n".join(status_lines)
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

            if all_done:
                break

            await asyncio.sleep(30)


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
        workflows=[AgentTaskWorkflow, AgentStepWorkflow, DeploymentBuildWorkflow, DeploymentRunWorkflow, TaskForceWorkflow],
        activities=[
            initialize_task,
            start_agent_container,
            poll_agent_turns,
            collect_agent_result,
            record_agent_turn,
            store_task_output,
            get_last_iteration,
            create_capability_request,
            build_agent_image,
            update_task_policy,
            finalize_task,
            create_deployment,
            build_deployment_image,
            start_deployment_container,
            stop_deployment_container,
            # Task Force activities
            load_task_force,
            start_member_task,
            persist_member_workflow_id,
            update_member_status,
            poll_member_task_status,
            execute_ceremony,
            finalize_task_force,
            post_task_force_progress,
            update_coordinator_task_status,
            check_task_force_deploy_role,
            write_ceremony_artifact,
            generate_ceremony_plan,
            collect_member_outputs,
            read_verdict_file,
            create_rework_tasks,
            # Ceremony State API activities
            post_agent_state_exchange,
            check_verdict_guard,
        ],
    )
    
    logger.info(f"Worker starting on task queue: {TASK_QUEUE}")
    
    # Run worker
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

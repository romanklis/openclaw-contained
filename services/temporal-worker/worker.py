"""
Temporal Worker - Executes workflows and activities
"""
import asyncio
import logging
from temporalio import workflow, activity
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
# Workflows
# =============================================================================

@workflow.defn
class AgentTaskWorkflow:
    """Main workflow for agent task execution"""
    
    def __init__(self):
        self.approval_received = False
        self.capability_approved = False
        self.current_image = "localhost:5000/openclaw-agent:openclaw"  # Track current agent image
        self.llm_model = "gemma3:4b"  # Track LLM model
        self.follow_up = ""  # Follow-up instructions for continuation
    
    @workflow.run
    async def run(
        self,
        task_id: str,
        llm_model: str = "gemma3:4b",
        current_image: str = "",
        follow_up: str = "",
    ) -> Dict[str, Any]:
        """Execute agent task.

        For first-run workflows ``current_image`` and ``follow_up`` are empty.
        For continuation workflows they carry over state from the previous run:
        - ``current_image``: the last built agent image (all packages installed)
        - ``follow_up``: user's follow-up instructions
        """
        
        self.llm_model = llm_model
        self.follow_up = follow_up

        # If continuing, pick up from the previous image instead of the base
        if current_image:
            self.current_image = current_image
            logger.info(f"♻️  CONTINUATION workflow for task {task_id} | image={current_image} | follow_up={follow_up[:120]}...")
        else:
            logger.info(f"Starting workflow for task {task_id} with model {llm_model}")
        
        # Step 1: Initialize task
        await workflow.execute_activity(
            initialize_task,
            args=[task_id],
            start_to_close_timeout=timedelta(seconds=30)
        )
        
        # Determine starting iteration.
        # For continuations, fetch the last iteration number so we don't overwrite.
        start_iteration = 0
        if current_image:  # this is a continuation
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
            result = await workflow.execute_child_workflow(
                AgentStepWorkflow.run,
                args=[task_id, iteration, self.current_image, self.llm_model, self.follow_up],
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
                    new_image = await workflow.execute_activity(
                        build_agent_image,
                        args=[task_id, capability, self.current_image],
                        start_to_close_timeout=timedelta(minutes=10)
                    )
                    
                    # Update current image for subsequent iterations
                    self.current_image = new_image
                    logger.info(f"Updated task image to {new_image}")
                    
                    # Update policy
                    await workflow.execute_activity(
                        update_task_policy,
                        args=[task_id, capability, new_image],
                        start_to_close_timeout=timedelta(seconds=30)
                    )
                    
                    logger.info(f"Task {task_id} resumed with new capability")
                else:
                    logger.info(f"Capability request denied for task {task_id}")
                
                # Reset approval flags
                self.approval_received = False
                self.capability_approved = False
        
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
        docker_client = docker.from_env()

        # --- resolve image (try name variants) ---
        image_found = False
        image_variants = [
            agent_image,
            agent_image.replace("localhost:5000/", "registry:5000/"),
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
            agent_image_fixed = agent_image.replace("localhost:5000/", "registry:5000/")
            if not agent_image_fixed.startswith("registry:5000/"):
                agent_image_fixed = f"registry:5000/{agent_image_fixed}"
            logger.info(f"📥 Pulling {agent_image_fixed}")
            docker_client.images.pull(agent_image_fixed)
            agent_image = agent_image_fixed

        # --- workspace ---
        workspaces_root = "/workspaces"
        workspace_id = ""
        task_description = ""
        try:
            import httpx as _httpx
            _cp_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            async with _httpx.AsyncClient(timeout=10.0) as _client:
                _resp = await _client.get(f"{_cp_url}/api/tasks/{task_id}")
                if _resp.status_code == 200:
                    _task_data = _resp.json()
                    workspace_id = _task_data.get("workspace_id", "")
                    task_description = _task_data.get("description", "")
        except Exception as _e:
            logger.warning(f"⚠️ Could not fetch task details: {_e}")

        if not workspace_id:
            workspace_id = f"workspace-{task_id}"

        workspace_dir = os.path.join(workspaces_root, workspace_id)
        os.makedirs(workspace_dir, exist_ok=True)
        os.chmod(workspace_dir, 0o777)

        # --- control-plane IP ---
        control_plane_ip = os.getenv("CONTROL_PLANE_IP", "")
        if not control_plane_ip:
            import socket
            try:
                control_plane_ip = socket.gethostbyname("control-plane")
            except socket.gaierror:
                control_plane_ip = "control-plane"
        cp_url_for_agent = f"http://{control_plane_ip}:8000"
        llm_router_url = f"{cp_url_for_agent}/api/llm"

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
        }

        logger.info(f"🚀 Launching container (detached)...")
        container = docker_client.containers.run(
            agent_image,
            environment=agent_env,
            volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
            tmpfs={"/tmp": "size=100m,mode=1777"},
            network_mode="host",
            detach=True,
        )

        logger.info(f"✅ Container started: {container.short_id}")
        return {
            "container_id": container.id,
            "workspace_dir": workspace_dir,
            "agent_image": agent_image,
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

    docker_client = docker.from_env()
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

    docker_client = docker.from_env()
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
) -> str:
    """Build new agent image with capability.
    
    Uses current_image as the base so capabilities accumulate
    incrementally: base → v1 (+ redis) → v2 (+ flask) → v3 ...
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
        
        # Call image builder service
        async with httpx.AsyncClient(timeout=300.0) as client:
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
                    return image_tag
                elif status["status"] == "failed":
                    error = status.get("error", "Unknown error")
                    logger.error(f"❌ BUILD_FAILED | Task: {task_id} | Error: {error}")
                    raise Exception(f"Image build failed: {error}")
                elif waited % 15 == 0:  # Log every 15 seconds
                    logger.info(f"   └─ Build in progress... ({waited}s elapsed)")
            
            raise Exception("Build timeout after 10 minutes")
            
    except Exception as e:
        logger.error(f"❌ BUILD_ERROR | Task: {task_id} | {e}")
        logger.warning(f"⚠️  FALLBACK | Task: {task_id} | Continuing with base image")
        # Fall back to base image
        return "localhost:5000/openclaw-agent:base"


@activity.defn
async def update_task_policy(
    task_id: str,
    capability: Dict[str, Any],
    new_image: str
) -> Dict[str, Any]:
    """Update task policy with new capability"""
    logger.info(f"Updating policy for task {task_id}")
    
    # TODO: Call control plane to update policy
    # TODO: Update task with new image reference
    
    return {"updated": True}


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
        
        docker_client = docker.from_env()
        
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
        
        docker_client = docker.from_env()
        
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
        workflows=[AgentTaskWorkflow, AgentStepWorkflow, DeploymentBuildWorkflow, DeploymentRunWorkflow],
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
        ],
    )
    
    logger.info(f"Worker starting on task queue: {TASK_QUEUE}")
    
    # Run worker
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

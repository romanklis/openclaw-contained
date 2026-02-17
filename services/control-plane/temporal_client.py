"""
Temporal client for workflow management
"""
from temporalio.client import Client
from config import settings
import logging

logger = logging.getLogger(__name__)


async def get_temporal_client() -> Client:
    """Get Temporal client"""
    client = await Client.connect(settings.TEMPORAL_HOST)
    return client


async def start_task_workflow(task_id: str, llm_model: str = "gemma3:4b") -> str:
    """Start a task workflow"""
    client = await get_temporal_client()
    
    workflow_id = f"task-workflow-{task_id}"
    
    # Start the workflow with task_id and llm_model as args list
    handle = await client.start_workflow(
        "AgentTaskWorkflow",
        args=[task_id, llm_model],
        id=workflow_id,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )
    
    logger.info(f"Started workflow {workflow_id} for task {task_id} with model {llm_model}")
    
    return workflow_id


async def continue_task_workflow(
    task_id: str,
    llm_model: str = "gemma3:4b",
    current_image: str = "",
    follow_up: str = "",
    continuation_number: int = 1,
) -> str:
    """Start a continuation workflow for an already-completed task.

    Uses a unique workflow ID so Temporal doesn't reject it as a duplicate.
    Passes current_image so the agent resumes from the last built image,
    and follow_up so the agent knows what to fix/improve.
    """
    client = await get_temporal_client()

    workflow_id = f"task-workflow-{task_id}-cont-{continuation_number}"

    handle = await client.start_workflow(
        "AgentTaskWorkflow",
        args=[task_id, llm_model, current_image, follow_up],
        id=workflow_id,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )

    logger.info(
        f"Started continuation workflow {workflow_id} for task {task_id} "
        f"(cont #{continuation_number}, image={current_image[:60]})"
    )

    return workflow_id

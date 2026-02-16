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

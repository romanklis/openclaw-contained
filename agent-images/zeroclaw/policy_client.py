"""
Policy Client for OpenClaw Agents
Version: 1.0.0
"""
import httpx
from typing import Dict, Any, Optional


class PolicyClient:
    """Client for interacting with OpenClaw Policy Engine"""
    
    def __init__(self, policy_engine_url: str, task_id: str):
        self.policy_engine_url = policy_engine_url
        self.task_id = task_id
    
    async def check_action(self, action: str, resource: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """Check if an action is allowed"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.policy_engine_url}/evaluate",
                json={
                    "task_id": self.task_id,
                    "action": action,
                    "resource": resource,
                    "context": context or {}
                }
            )
            result = response.json()
            return result.get("allowed", False)
    
    async def get_current_policy(self) -> Dict[str, Any]:
        """Get current policy for the task"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.policy_engine_url}/policies/{self.task_id}"
            )
            return response.json()

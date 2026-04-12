"""
OpenClaw Agent Runtime
Version: 1.0.0

This is the main runtime for OpenClaw agents.
It connects to the control plane and executes tasks with policy enforcement.
"""
import os
import sys
import time
import logging
from typing import Dict, Any, Optional
import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("openclaw.agent")


class AgentRuntime:
    """OpenClaw Agent Runtime"""
    
    def __init__(self):
        self.task_id = os.getenv("TASK_ID")
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
        self.policy_engine_url = os.getenv("POLICY_ENGINE_URL", "http://policy-engine:8001")
        self.workspace = os.getenv("OPENCLAW_WORKSPACE", "/workspace")
        self.llm_model = os.getenv("LLM_MODEL", "gemini-2.0-flash-exp")
        self.llm_providers = None
        
        if not self.task_id:
            raise ValueError("TASK_ID environment variable is required")
        
        logger.info(f"🚀 Agent runtime initialized for task {self.task_id}")
        logger.info(f"   Model: {self.llm_model}")
        logger.info(f"   Control Plane: {self.control_plane_url}")
        logger.info(f"   Workspace: {self.workspace}")
    
    async def check_policy(self, action: str, resource: str) -> bool:
        """Check if action is allowed by policy"""
        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"🔒 Policy check: {action} on {resource}")
                response = await client.post(
                    f"{self.policy_engine_url}/evaluate",
                    json={
                        "task_id": self.task_id,
                        "action": action,
                        "resource": resource
                    }
                )
                result = response.json()
                allowed = result.get("allowed", False)
                logger.info(f"   → {'✓ Allowed' if allowed else '✗ Denied'}")
                return allowed
            except Exception as e:
                logger.error(f"❌ Policy check failed: {e}")
                return False
    
    async def request_capability(self, capability: Dict[str, Any]) -> bool:
        """Request a new capability"""
        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"📋 Requesting capability: {capability.get('capability_type')} - {capability.get('resource_name')}")
                logger.info(f"   Justification: {capability.get('justification', 'N/A')}")
                response = await client.post(
                    f"{self.control_plane_url}/api/capabilities/requests",
                    json={
                        "task_id": self.task_id,
                        **capability
                    }
                )
                success = response.status_code == 201
                logger.info(f"   → {'✓ Requested' if success else '✗ Failed'}")
                return success
            except Exception as e:
                logger.error(f"❌ Capability request failed: {e}")
                return False
    
    async def fetch_llm_providers(self):
        """Fetch LLM provider configuration from control plane"""
        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"🔌 Fetching LLM provider configuration...")
                response = await client.get(f"{self.control_plane_url}/api/llm/providers")
                if response.status_code == 200:
                    self.llm_providers = response.json()
                    logger.info(f"   → ✓ Loaded {len(self.llm_providers.get('providers', []))} providers")
                    return True
                else:
                    logger.warning(f"   → Failed to fetch provider config: {response.status_code}")
                    return False
            except Exception as e:
                logger.warning(f"   → Failed to fetch provider config: {e}")
                return False
    
    async def run(self):
        """Main agent execution loop"""
        logger.info(f"═══════════════════════════════════════════════════════════")
        logger.info(f"🤖 OPENCLAW AGENT STARTING")
        logger.info(f"   Task ID: {self.task_id}")
        logger.info(f"   Model: {self.llm_model}")
        logger.info(f"═══════════════════════════════════════════════════════════")
        
        # Fetch LLM provider configuration
        await self.fetch_llm_providers()
        
        iteration = 0
        max_iterations = int(os.getenv("MAX_ITERATIONS", "50"))
        
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"")
            logger.info(f"─── Iteration {iteration}/{max_iterations} ───────────────────────────────────")
            
            # Agent execution logic would go here
            # This is a stub that demonstrates the pattern
            
            await self.execute_iteration(iteration)
            
            time.sleep(1)
        
        logger.info(f"")
        logger.info(f"═══════════════════════════════════════════════════════════")
        logger.info(f"✓ Agent completed after {iteration} iterations")
        logger.info(f"═══════════════════════════════════════════════════════════")
    
    async def execute_iteration(self, iteration: int):
        """Execute one iteration of agent logic"""
        # This stub should NOT be used in production — the real entrypoint
        # is taskforge-adapter.py.  If we reach here, exit cleanly so we
        # don't accidentally create capability requests in a loop.
        logger.warning(
            "⚠️ agent_runtime.py stub invoked — this image is missing "
            "taskforge-adapter.py as its ENTRYPOINT.  Exiting."
        )
        import sys
        sys.exit(0)


def main():
    """Main entry point"""
    try:
        import asyncio
        runtime = AgentRuntime()
        asyncio.run(runtime.run())
    except Exception as e:
        logger.error(f"Agent runtime failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
OpenClaw Agent Executor
Runs inside agent containers to execute tasks using LLM
"""
import os
import json
import asyncio
import httpx
from typing import Dict, Any, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentExecutor:
    def __init__(self, task_id: str, workspace_path: str = "/workspace"):
        self.task_id = task_id
        self.workspace_path = workspace_path
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://openclaw-control-plane:8000")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
        self.model = os.getenv("LLM_MODEL", "gemma3:4b")  # Use available model
        self.max_iterations = 50
        self.iteration = 0
        
        logger.info(f"AgentExecutor initialized for task {task_id}")
        logger.info(f"  Ollama: {self.ollama_url}")
        logger.info(f"  Model: {self.model}")
        logger.info(f"  Control Plane: {self.control_plane_url}")
        
    async def get_task(self) -> Dict[str, Any]:
        """Fetch task details from control plane"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.control_plane_url}/api/tasks/{self.task_id}")
            response.raise_for_status()
            return response.json()
    
    async def request_capability(self, capability_type: str, resource: str, justification: str) -> bool:
        """Request a capability and wait for approval"""
        logger.info(f"Requesting capability: {capability_type} - {resource}")
        
        payload = {
            "type": capability_type,
            "resource": resource,
            "justification": justification
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.control_plane_url}/api/tasks/{self.task_id}/capabilities",
                json=payload,
                timeout=10.0
            )
            response.raise_for_status()
            
        # Signal that capability was requested
        return True
    
    async def call_llm(self, messages: List[Dict[str, str]]) -> str:
        """Call Ollama LLM"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False
                }
            )
            response.raise_for_status()
            result = response.json()
            return result["message"]["content"]
    
    async def execute_step(self, task_description: str, history: List[Dict]) -> Dict[str, Any]:
        """Execute one reasoning step"""
        self.iteration += 1
        logger.info(f"Agent step {self.iteration}/{self.max_iterations}")
        
        # Build prompt with task and history
        messages = [
            {
                "role": "system",
                "content": f"""You are an AI agent executing tasks in a sandboxed environment.
                
Task: {task_description}

Workspace: {self.workspace_path}

Available capabilities:
- Python code execution (default)
- Tool installation (requires approval): pandas, numpy, requests, etc.
- File operations: read/write in {self.workspace_path}

When you need a tool that's not installed:
1. Output: NEED_CAPABILITY: tool_install: <package_name>
2. Justify why you need it
3. Wait for approval

When task is complete, output: TASK_COMPLETE

Your response should be a JSON with:
{{"thought": "your reasoning", "action": "execute_python|request_capability|complete", "code": "...", "capability": {{...}}}}
"""
            }
        ]
        
        # Add history
        for entry in history[-5:]:  # Last 5 steps only
            messages.append({"role": "user", "content": json.dumps(entry)})
        
        # Get LLM response
        try:
            response = await self.call_llm(messages)
            logger.info(f"LLM response: {response[:200]}")
            
            # Parse response
            # Try to extract JSON
            if "{" in response and "}" in response:
                start = response.find("{")
                end = response.rfind("}") + 1
                json_str = response[start:end]
                result = json.loads(json_str)
            else:
                result = {"thought": response, "action": "complete"}
            
            return result
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return {"error": str(e), "action": "complete"}
    
    async def run(self) -> Dict[str, Any]:
        """Main execution loop"""
        logger.info(f"Starting agent execution for task {self.task_id}")
        
        try:
            # Get task details
            task = await self.get_task()
            task_description = task.get("description", "No description")
            
            history = []
            completed = False
            capability_requested = False
            
            while self.iteration < self.max_iterations and not completed:
                step_result = await self.execute_step(task_description, history)
                history.append(step_result)
                
                action = step_result.get("action", "")
                
                if action == "complete" or "TASK_COMPLETE" in str(step_result):
                    completed = True
                    logger.info("Task completed")
                    
                elif action == "request_capability":
                    capability = step_result.get("capability", {})
                    if capability:
                        capability_requested = await self.request_capability(
                            capability.get("type", "tool_install"),
                            capability.get("resource", ""),
                            capability.get("justification", "Agent requested")
                        )
                        if capability_requested:
                            # Stop execution, wait for approval
                            logger.info("Capability requested, stopping execution")
                            break
                
                elif action == "execute_python":
                    # TODO: Execute code safely
                    code = step_result.get("code", "")
                    logger.info(f"Would execute code: {code[:100]}")
                
                # Small delay between iterations
                await asyncio.sleep(0.5)
            
            return {
                "completed": completed,
                "capability_requested": capability_requested,
                "iterations": self.iteration,
                "history": history[-3:] if history else []
            }
            
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            return {
                "completed": False,
                "capability_requested": False,
                "error": str(e)
            }


async def main():
    """CLI entry point"""
    task_id = os.getenv("TASK_ID")
    if not task_id:
        logger.error("TASK_ID environment variable required")
        return
    
    executor = AgentExecutor(task_id)
    result = await executor.run()
    
    # Write result to file for temporal worker to read
    # Worker expects result.json in /tmp
    result_path = "/tmp/result.json"
    with open(result_path, "w") as f:
        json.dump(result, f)
    
    logger.info(f"Result written to {result_path}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env node
/**
 * OpenClaw Wrapper for Secure Orchestration Platform
 * 
 * This wrapper:
 * 1. Initializes OpenClaw with task context
 * 2. Intercepts capability requests (tool installations, file access, etc.)
 * 3. Requests approval from control-plane
 * 4. Reports completion status
 */

const fs = require('fs').promises;
const path = require('path');
const axios = require('axios');

// Configuration from environment
const config = {
  taskId: process.env.TASK_ID,
  controlPlaneUrl: process.env.CONTROL_PLANE_URL,
  ollamaUrl: process.env.OLLAMA_URL,
  llmModel: process.env.LLM_MODEL || 'gemma3:4b',
  workspace: process.env.WORKSPACE || '/workspace',
  maxIterations: parseInt(process.env.MAX_ITERATIONS || '50'),
};

console.log('OpenClaw Wrapper initialized');
console.log(`  Task ID: ${config.taskId}`);
console.log(`  Control Plane: ${config.controlPlaneUrl}`);
console.log(`  LLM Model: ${config.llmModel}`);
console.log(`  Workspace: ${config.workspace}`);

/**
 * Fetch task details from control-plane
 */
async function getTask() {
  try {
    const response = await axios.get(
      `${config.controlPlaneUrl}/api/tasks/${config.taskId}`
    );
    return response.data;
  } catch (error) {
    console.error('Failed to fetch task:', error.message);
    throw error;
  }
}

/**
 * Request capability approval from control-plane
 */
async function requestCapability(capabilityType, resource, justification) {
  console.log(`Requesting capability: ${capabilityType} - ${resource}`);
  
  try {
    const response = await axios.post(
      `${config.controlPlaneUrl}/api/tasks/${config.taskId}/capabilities`,
      {
        type: capabilityType,
        resource: resource,
        justification: justification
      }
    );
    
    return response.data;
  } catch (error) {
    console.error('Capability request failed:', error.message);
    return { approved: false, error: error.message };
  }
}

/**
 * OpenClaw capability interceptor
 * Intercepts when OpenClaw tries to use tools/packages it doesn't have
 */
class CapabilityInterceptor {
  constructor() {
    this.requestedCapabilities = new Set();
  }

  /**
   * Check if a capability is available
   */
  async checkCapability(type, resource) {
    // Check if already requested in this session
    const key = `${type}:${resource}`;
    if (this.requestedCapabilities.has(key)) {
      return true; // Already requested and approved
    }

    // For now, return false to trigger capability request
    return false;
  }

  /**
   * Request a new capability
   */
  async request(type, resource, justification) {
    const key = `${type}:${resource}`;
    
    // Request approval
    const result = await requestCapability(type, resource, justification);
    
    if (result.approved) {
      this.requestedCapabilities.add(key);
      return {
        capability_requested: true,
        capability: { type, resource, justification },
        message: 'Capability approved, container will be rebuilt'
      };
    }
    
    return {
      capability_requested: true,
      capability_denied: true,
      message: 'Capability denied or pending approval'
    };
  }
}

/**
 * Call Ollama LLM via HTTP
 */
async function callOllama(prompt, conversationHistory = []) {
  try {
    const messages = [
      ...conversationHistory,
      { role: 'user', content: prompt }
    ];

    const response = await axios.post(
      `${config.ollamaUrl}/api/chat`,
      {
        model: config.llmModel,
        messages: messages,
        stream: false
      }
    );

    return response.data.message.content;
  } catch (error) {
    console.error('Ollama call failed:', error.message);
    throw error;
  }
}

/**
 * OpenClaw Agent - Simulated integration
 * 
 * In production, this would integrate with the actual OpenClaw runtime
 * from /opt/openclaw. For now, we simulate the agent behavior:
 * 1. Use LLM to analyze task
 * 2. Generate and execute code
 * 3. Detect capability requests (missing packages)
 * 4. Request approval and trigger rebuild
 */
async function runOpenClawAgent(task, interceptor) {
  const conversationHistory = [];
  const result = {
    completed: false,
    capability_requested: false,
    iterations: 0,
    history: [],
    output: null
  };

  // System prompt that mimics OpenClaw agent behavior
  const systemPrompt = `You are OpenClaw, an AI agent running in a secure, read-only container.

Task: ${task.description}

You have access to:
- Python 3 interpreter
- Basic Linux tools (bash, ls, cat, etc.)
- File system (read/write in /workspace only)

IMPORTANT:
- If you need a Python package that's not installed, respond with: CAPABILITY_REQUEST:package:PACKAGE_NAME:JUSTIFICATION
- Example: CAPABILITY_REQUEST:package:pandas:Need to analyze CSV data
- After requesting a capability, explain that the container will be rebuilt with the package

Generate Python code to solve the task. Be concise.`;

  console.log('\\nStarting OpenClaw agent execution...');
  
  for (let i = 0; i < config.maxIterations; i++) {
    result.iterations++;
    console.log(`\\n=== Iteration ${result.iterations} ===`);

    // Get agent decision from LLM
    const prompt = i === 0 
      ? systemPrompt 
      : `Continue working on the task. Previous result: ${result.history[result.history.length - 1]?.output || 'Starting'}`;

    const agentResponse = await callOllama(prompt, conversationHistory);
    console.log(`Agent: ${agentResponse.substring(0, 200)}...`);

    conversationHistory.push(
      { role: 'user', content: prompt },
      { role: 'assistant', content: agentResponse }
    );

    // Check for capability request
    const capabilityMatch = agentResponse.match(/CAPABILITY_REQUEST:(\w+):([^:]+):(.+)/);
    if (capabilityMatch) {
      const [_, type, resource, justification] = capabilityMatch;
      console.log(`\\n🔒 Capability requested: ${type} - ${resource}`);
      console.log(`   Justification: ${justification}`);

      const capResult = await interceptor.request(type, resource, justification);
      result.capability_requested = true;
      result.requested_capability = { type, resource, justification };
      result.history.push({
        iteration: result.iterations,
        action: 'capability_request',
        capability: { type, resource, justification },
        result: capResult
      });

      // Stop execution - container will be rebuilt
      console.log('\\n⏸️  Pausing execution for capability approval and rebuild');
      break;
    }

    // Extract code from response
    const codeMatch = agentResponse.match(/```python\\n([\\s\\S]+?)```/) || 
                      agentResponse.match(/```\\n([\\s\\S]+?)```/);
    
    if (codeMatch) {
      const code = codeMatch[1];
      console.log(`\\nExecuting code:\\n${code}\\n`);

      try {
        // Execute Python code
        const { execSync } = require('child_process');
        const output = execSync('python3 -c ' + JSON.stringify(code), {
          cwd: config.workspace,
          encoding: 'utf8',
          timeout: 30000,
          maxBuffer: 1024 * 1024
        });

        console.log(`Output: ${output}`);
        result.history.push({
          iteration: result.iterations,
          action: 'execute_code',
          code: code,
          output: output
        });

        // Check if task is complete
        if (agentResponse.toLowerCase().includes('task complete') || 
            agentResponse.toLowerCase().includes('finished')) {
          result.completed = true;
          result.output = output;
          console.log('\\n✅ Task completed successfully!');
          break;
        }

      } catch (error) {
        const errorMsg = error.message;
        console.error(`Execution error: ${errorMsg}`);

        // Check for missing package errors
        const packageError = errorMsg.match(/ModuleNotFoundError: No module named '([^']+)'/) ||
                            errorMsg.match(/ImportError: No module named '([^']+)'/);
        
        if (packageError) {
          const packageName = packageError[1];
          console.log(`\\n🔒 Detected missing package: ${packageName}`);
          
          const capResult = await interceptor.request(
            'package',
            packageName,
            `Required for task: ${task.description}`
          );

          result.capability_requested = true;
          result.requested_capability = { 
            type: 'package', 
            resource: packageName, 
            justification: `Required for task: ${task.description}` 
          };
          result.history.push({
            iteration: result.iterations,
            action: 'capability_request',
            capability: { type: 'package', resource: packageName },
            result: capResult
          });

          console.log('\\n⏸️  Pausing execution for capability approval and rebuild');
          break;
        }

        result.history.push({
          iteration: result.iterations,
          action: 'execute_code',
          code: code,
          error: errorMsg
        });
      }
    } else {
      // No code to execute, just agent reasoning
      result.history.push({
        iteration: result.iterations,
        action: 'reasoning',
        content: agentResponse
      });
    }
  }

  return result;
}

/**
 * Main execution function
 */
async function main() {
  try {
    // Fetch task
    console.log('Fetching task details...');
    const task = await getTask();
    console.log(`Task: ${task.name}`);
    console.log(`Description: ${task.description}`);

    // Initialize capability interceptor
    const interceptor = new CapabilityInterceptor();

    // Run OpenClaw agent
    const result = await runOpenClawAgent(task, interceptor);

    // Write result for temporal worker to read
    const resultPath = '/tmp/result.json';
    await fs.writeFile(resultPath, JSON.stringify(result, null, 2));
    console.log(`\\nResult written to ${resultPath}`);

    // Also output to stdout
    console.log(JSON.stringify(result, null, 2));

    process.exit(0);
  } catch (error) {
    console.error('Execution failed:', error);
    
    const errorResult = {
      completed: false,
      capability_requested: false,
      error: error.message,
      stack: error.stack
    };
    
    try {
      await fs.writeFile('/tmp/result.json', JSON.stringify(errorResult, null, 2));
    } catch (writeError) {
      console.error('Failed to write error result:', writeError);
    }
    
    console.log(JSON.stringify(errorResult, null, 2));
    process.exit(1);
  }
}

// Run main function
main();

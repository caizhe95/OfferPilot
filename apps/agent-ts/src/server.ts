/**
 * Agent server entry point - Express.js service for the agent runtime.
 * Uses @earendil-works/pi-agent-core for agent loop and @earendil-works/pi-ai for LLM providers.
 */

import express, { Request, Response } from "express";
import { type Model, getModel } from "@earendil-works/pi-ai/compat";
import { Agent } from "./agent";
import { createToolRegistry } from "./tool-registry";
import { FastApiToolClient } from "./fastapi-client";
import type { ToolExecutor } from "./types";

// Configuration
const PORT = parseInt(process.env.AGENT_PORT || "3001", 10);
const FASTAPI_URL =
  process.env.FASTAPI_BASE_URL || process.env.FASTAPI_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Model setup
// ---------------------------------------------------------------------------

function createModel(): Model<any> {
  const modelId = process.env.OPENAI_MODEL || "gpt-4o";
  return getModel("openai", modelId as any);
}

// ---------------------------------------------------------------------------
// Tool executors
// ---------------------------------------------------------------------------

function buildToolExecutors(): Record<string, ToolExecutor> {
  const client = new FastApiToolClient({ baseUrl: FASTAPI_URL });
  return client.getExecutors();
}

// ---------------------------------------------------------------------------
// Agent factory
// ---------------------------------------------------------------------------

function createAgent(): Agent {
  const toolExecutors = buildToolExecutors();
  const registry = createToolRegistry(toolExecutors);

  const model = createModel();

  const agent = new Agent({
    maxSteps: 6,
    maxToolCalls: 3,
    systemPrompt: `You are an AI Agent / LLM engineering interview diagnosis expert.
Your task is to diagnose a candidate's interview answer for AI Agent / LLM engineering positions.

You can:
1. Use search_knowledge to find relevant interview knowledge topics.
2. Score the answer on content dimensions: concept_accuracy, structure_completeness, engineering_depth, example_quality, question_alignment.
3. Score the answer on voice dimensions: fluency, filler_words, redundancy, spoken_clarity, answer_pacing.
4. Generate likely follow-up questions.
5. Produce a final Markdown diagnosis report.

Always produce output in Chinese. Keep responses under 2500 characters.`,
    model,
  });

  // Register tools
  const schemas = registry.getSchemas();
  for (const schema of schemas) {
    const tool = registry.get(schema.name);
    if (tool) {
      agent.addTools(tool);
    }
  }

  return agent;
}

// ---------------------------------------------------------------------------
// Express server
// ---------------------------------------------------------------------------

const app = express();
app.use(express.json());

// Agent run endpoint (non-streaming)
app.post("/agent/run", async (req: Request, res: Response) => {
  try {
    const { input, session_id } = req.body;
    if (!input) {
      res.status(400).json({ error: "input is required" });
      return;
    }

    const agent = createAgent();
    const result = await agent.run(input);

    res.json({
      session_id: result.sessionId,
      success: result.success,
      events: result.events,
      final_output: result.finalOutput,
      steps_taken: result.stepsTaken,
      tool_calls_made: result.toolCallsMade,
      error: result.error,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    res.status(500).json({ error: message });
  }
});

// Agent run-stream endpoint (SSE)
app.post("/agent/run-stream", async (req: Request, res: Response) => {
  try {
    const { input } = req.body;
    if (!input) {
      res.status(400).json({ error: "input is required" });
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    const agent = createAgent();
    const result = await agent.run(input, (event) => {
      res.write(`data: ${JSON.stringify(event)}\n\n`);
    });

    res.write(
      `data: ${JSON.stringify({
        type: "run_complete",
        session_id: result.sessionId,
        success: result.success,
        steps_taken: result.stepsTaken,
        tool_calls_made: result.toolCallsMade,
        error: result.error,
      })}\n\n`,
    );
    res.end();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    res.write(`data: ${JSON.stringify({ type: "error", message })}\n\n`);
    res.end();
  }
});

// Health check
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", mode: "live" });
});

// List tools
app.get("/agent/tools", (_req: Request, res: Response) => {
  const registry = createToolRegistry(buildToolExecutors());
  const tools = registry.list();
  res.json({ tools });
});

app.listen(PORT, () => {
  console.log(`OfferPilot Agent Service running on port ${PORT}`);
  console.log(`Mode: live`);
  console.log(`FastAPI URL: ${FASTAPI_URL}`);
});

export default app;

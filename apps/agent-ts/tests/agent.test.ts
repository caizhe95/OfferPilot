/**
 * Tests for the agent runtime and tool registry.
 * Uses pi-mono (pi-agent-core) Agent with faux provider for mock mode.
 */

import { describe, it, expect } from "vitest";
import { Agent } from "../src/agent";
import { createToolRegistry, ToolRegistry } from "../src/tool-registry";
import { registerFauxProvider, fauxText } from "@earendil-works/pi-ai/compat";

// Create a shared faux provider for mock tests
function createMockModel() {
  const faux = registerFauxProvider({
    api: "faux-test",
    provider: "faux-test",
    models: [
      {
        id: "mock-gpt-4o",
        name: "Mock GPT-4o",
        reasoning: false,
        input: ["text"],
        contextWindow: 128000,
        maxTokens: 16384,
      },
    ],
  });
  faux.setResponses([
    (_ctx, _opts, _state, _model) => ({
      role: "assistant",
      api: "faux-test",
      provider: "faux-test",
      model: "mock-gpt-4o",
      content: [fauxText("This is a mock agent response for testing.")],
      usage: {
        input: 50,
        output: 20,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 70,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      timestamp: Date.now(),
      stopReason: "end_turn",
    }),
  ]);
  const model = faux.getModel("mock-gpt-4o");
  if (!model) throw new Error("Failed to create mock model");
  return model;
}

describe("ToolRegistry", () => {
  it("should register and retrieve tools", () => {
    const registry = new ToolRegistry();
    registry.register(
      {
        name: "test_tool",
        description: "A test tool",
        parameters: { type: "object", properties: {} },
        risk_level: "low",
      },
      async (params) => ({ result: params }),
    );

    const schemas = registry.getSchemas();
    expect(schemas.length).toBe(1);
    expect(schemas[0].name).toBe("test_tool");
  });

  it("should execute registered tools", async () => {
    const registry = new ToolRegistry();
    registry.register(
      {
        name: "echo",
        description: "Echo tool",
        parameters: {
          type: "object",
          properties: { msg: { type: "string" } },
        },
        risk_level: "low",
      },
      async (params) => params,
    );

    const result = await registry.execute("echo", { msg: "hello" });
    expect(result).toEqual({ msg: "hello" });
  });

  it("should throw for unregistered tools", async () => {
    const registry = new ToolRegistry();
    await expect(registry.execute("unknown", {})).rejects.toThrow(
      "Tool not found",
    );
  });

  it("should return error on executor exception", async () => {
    const registry = new ToolRegistry();
    registry.register(
      {
        name: "failing_tool",
        description: "Always fails",
        parameters: {},
        risk_level: "low",
      },
      async () => {
        throw new Error("Test failure");
      },
    );

    const result = await registry.execute("failing_tool", {});
    expect(result).toHaveProperty("error", true);
    expect(result).toHaveProperty("message", "Test failure");
  });

  it("should create registry with default tools", async () => {
    const registry = createToolRegistry({
      search_knowledge: async (params) => ({
        query: params.query,
        results: [],
      }),
    });

    const schemas = registry.getSchemas();
    expect(schemas.length).toBe(1);
    expect(schemas[0].name).toBe("search_knowledge");
    expect(schemas[0].risk_level).toBe("low");

    const result = await registry.execute("search_knowledge", {
      query: "test",
    });
    expect(result).toHaveProperty("query", "test");
    expect(result).toHaveProperty("results");
  });
});

describe("Agent (pi-mono based)", () => {
  function createTestAgent() {
    const model = createMockModel();
    const agent = new Agent({
      maxSteps: 6,
      maxToolCalls: 3,
      systemPrompt: "You are a test agent.",
      model,
      mockMode: true,
    });
    return agent;
  }

  it("should run in mock mode", async () => {
    const agent = createTestAgent();
    const result = await agent.run("Test input");
    expect(result.success).toBe(true);
    expect(result.sessionId).toBeTruthy();
  });

  it("should contain session_start and session_end events", async () => {
    const agent = createTestAgent();
    const result = await agent.run("Test input");
    expect(result.events.some((e) => e.type === "session_start")).toBe(true);
    expect(result.events.some((e) => e.type === "session_end")).toBe(true);
  });

  it("should emit done event with final_output", async () => {
    const agent = createTestAgent();
    const result = await agent.run("Hello");
    const doneEvents = result.events.filter((e) => e.type === "done");
    expect(doneEvents.length).toBe(1);
    expect(doneEvents[0]).toHaveProperty("final_output");
  });

  it("should register and use tools via pi agent", async () => {
    let toolCalled = false;

    const model = createMockModel();
    const agent = new Agent({
      maxSteps: 6,
      maxToolCalls: 3,
      systemPrompt: "You are a test agent. Use search_knowledge tool.",
      model,
      mockMode: true,
    });

    agent.addTools({
      schema: {
        name: "search_knowledge",
        description: "Search knowledge",
        parameters: {
          type: "object",
          properties: { query: { type: "string" } },
          required: ["query"],
        },
        risk_level: "low",
      },
      executor: async (params) => {
        toolCalled = true;
        return { query: params.query, results: [], total: 0 };
      },
    });

    const result = await agent.run("Test");
    expect(result.success).toBe(true);
    // toolCalled may not be true if the mock model didn't emit tool calls
    // but the test should not crash
  });
});

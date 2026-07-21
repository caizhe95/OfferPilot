/**
 * Tool registry - manages registered tools with schemas and executors.
 */

import { RegisteredTool, ToolExecutor, ToolSchema } from "./types";

export class ToolRegistry {
  private tools: Map<string, RegisteredTool> = new Map();

  register(schema: ToolSchema, executor: ToolExecutor): void {
    this.tools.set(schema.name, { schema, executor });
  }

  getSchemas(): ToolSchema[] {
    return Array.from(this.tools.values()).map((t) => t.schema);
  }

  get(name: string): RegisteredTool | undefined {
    return this.tools.get(name);
  }

  async execute(name: string, params: Record<string, unknown>): Promise<unknown> {
    const tool = this.tools.get(name);
    if (!tool) {
      throw new Error(`Tool not found: ${name}`);
    }
    try {
      return await tool.executor(params);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      return { error: true, message, tool_name: name };
    }
  }

  list(): { name: string; risk_level: string; description: string }[] {
    return Array.from(this.tools.values()).map((t) => ({
      name: t.schema.name,
      risk_level: t.schema.risk_level,
      description: t.schema.description,
    }));
  }
}

/** Create the default tool registry with built-in tools. */
export function createToolRegistry(
  toolExecutors?: Record<string, ToolExecutor>
): ToolRegistry {
  const registry = new ToolRegistry();

  // Register knowledge search tool (LOW)
  if (toolExecutors?.search_knowledge) {
    registry.register(
      {
        name: "search_knowledge",
        description:
          "Search the knowledge base for AI Agent / LLM engineering interview topics. " +
          "Use FTS5 full-text search to find relevant content.",
        parameters: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Search query string (required)",
            },
            dimension: {
              type: "string",
              description: "Optional dimension filter",
            },
            limit: {
              type: "integer",
              description: "Max results, default 5, max 20",
            },
            source: {
              type: "string",
              description: "Optional source filter",
            },
          },
          required: ["query"],
        },
        risk_level: "low",
      },
      toolExecutors.search_knowledge
    );
  }

  // Register score_answer tool (LOW)
  if (toolExecutors?.score_answer) {
    registry.register(
      {
        name: "score_answer",
        description:
          "Score an interview answer on 5 content dimensions: concept_accuracy, " +
          "structure_completeness, engineering_depth, example_quality, question_alignment. " +
          "Returns dimension scores and explanations.",
        parameters: {
          type: "object",
          properties: {
            question: { type: "string", description: "The interview question" },
            answer: { type: "string", description: "The candidate's answer" },
            knowledge_context: {
              type: "array",
              description: "Optional knowledge entries for context",
            },
          },
          required: ["question", "answer"],
        },
        risk_level: "low",
      },
      toolExecutors.score_answer
    );
  }

  // Register analyze_voice_text tool (LOW)
  if (toolExecutors?.analyze_voice_text) {
    registry.register(
      {
        name: "analyze_voice_text",
        description:
          "Analyze voice dimensions from transcript text: fluency, filler_words, " +
          "redundancy, spoken_clarity, answer_pacing.",
        parameters: {
          type: "object",
          properties: {
            transcript: { type: "string", description: "The spoken answer transcript" },
          },
          required: ["transcript"],
        },
        risk_level: "low",
      },
      toolExecutors.analyze_voice_text
    );
  }

  // Register generate_followup tool (LOW)
  if (toolExecutors?.generate_followup) {
    registry.register(
      {
        name: "generate_followup",
        description:
          "Generate likely follow-up interview questions based on identified weaknesses.",
        parameters: {
          type: "object",
          properties: {
            question: { type: "string", description: "The original interview question" },
            answer: { type: "string", description: "The candidate's answer" },
            weaknesses: {
              type: "array",
              items: { type: "string" },
              description: "List of identified weakness descriptions",
            },
          },
          required: ["question", "answer"],
        },
        risk_level: "low",
      },
      toolExecutors.generate_followup
    );
  }

  // Register save_memory tool (HIGH)
  if (toolExecutors?.save_memory) {
    registry.register(
      {
        name: "save_memory",
        description:
          "Save a memory entry for future sessions. HIGH risk - requires user permission.",
        parameters: {
          type: "object",
          properties: {
            session_id: { type: "string", description: "Session ID" },
            key: { type: "string", description: "Memory key (e.g., weakness, strength)" },
            value: { type: "string", description: "Memory value" },
            category: { type: "string", description: "Category (e.g., diagnosis)" },
          },
          required: ["session_id", "key", "value"],
        },
        risk_level: "high",
      },
      toolExecutors.save_memory
    );
  }

  // Register export_report tool (HIGH)
  if (toolExecutors?.export_report) {
    registry.register(
      {
        name: "export_report",
        description:
          "Export a stored diagnosis report by ID. HIGH risk - requires user permission.",
        parameters: {
          type: "object",
          properties: {
            report_id: { type: "string", description: "Report ID to export" },
          },
          required: ["report_id"],
        },
        risk_level: "high",
      },
      toolExecutors.export_report
    );
  }

  return registry;
}

/**
 * FastAPI tool client - executes tools by calling the FastAPI backend.
 */

import type { ToolExecutor } from "./types";

export interface FastApiToolConfig {
  baseUrl: string;
}

export class FastApiToolClient {
  private baseUrl: string;

  constructor(config: FastApiToolConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, "");
  }

  private async call(
    path: string,
    method: string = "GET",
    body?: unknown,
  ): Promise<unknown> {
    const url = `${this.baseUrl}${path}`;
    const options: RequestInit = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(url, options);
    if (!response.ok) {
      const errorText = await response.text();
      return { error: true, status: response.status, message: errorText };
    }
    return response.json();
  }

  // -----------------------------------------------------------------------
  // Tool executors
  // -----------------------------------------------------------------------

  searchKnowledge: ToolExecutor = async (params) => {
    const query = params.query as string;
    const dimension = params.dimension as string | undefined;
    const limit = (params.limit as number) || 5;
    const source = params.source as string | undefined;

    const searchParams = new URLSearchParams({ query, limit: String(limit) });
    if (dimension) searchParams.set("dimension", dimension);
    if (source) searchParams.set("source", source);

    return this.call(
      `/api/tools/search-knowledge?${searchParams.toString()}`,
    );
  };

  scoreAnswer: ToolExecutor = async (params) => {
    return this.call("/api/tools/score-answer", "POST", {
      session_id: params.session_id || "default",
      question: params.question,
      answer: params.answer,
      knowledge_context: params.knowledge_context || null,
    });
  };

  analyzeVoiceText: ToolExecutor = async (params) => {
    return this.call("/api/tools/analyze-voice-text", "POST", {
      session_id: params.session_id || "default",
      transcript: params.transcript,
    });
  };

  generateFollowup: ToolExecutor = async (params) => {
    return this.call("/api/tools/generate-followup", "POST", {
      session_id: params.session_id || "default",
      question: params.question,
      answer: params.answer,
      weaknesses: params.weaknesses || null,
    });
  };

  saveMemory: ToolExecutor = async (params) => {
    return this.call("/api/tools/save-memory", "POST", {
      session_id: params.session_id,
      key: params.key,
      value: params.value,
      category: params.category || "general",
    });
  };

  exportReport: ToolExecutor = async (params) => {
    return this.call(
      `/api/tools/export-report?report_id=${encodeURIComponent((params.report_id as string) || "")}`,
      "GET",
    );
  };

  /**
   * Return executors for all supported tools.
   */
  getExecutors(): Record<string, ToolExecutor> {
    return {
      search_knowledge: this.searchKnowledge,
      score_answer: this.scoreAnswer,
      analyze_voice_text: this.analyzeVoiceText,
      generate_followup: this.generateFollowup,
      save_memory: this.saveMemory,
      export_report: this.exportReport,
    };
  }
}

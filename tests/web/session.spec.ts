import { expect, test, type Page, type Route } from "@playwright/test";
import { consumeSseWithRetry, SseDecoder, SseReconnectError } from "../../src/web/src/lib/sse";

const SESSION_ID = "session-e2e";
const RUN_ID = "run-e2e";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function event(type: string, data: Record<string, unknown>, sequence: number) {
  return `id: ${sequence}\ndata: ${JSON.stringify({ type, session_id: SESSION_ID, run_id: RUN_ID, sequence, created_at: "2026-08-04T00:00:00Z", data })}\n\n`;
}

async function mockApi(page: Page) {
  let reportReady = false;
  let messages: Array<Record<string, unknown>> = [];
  let run: Record<string, unknown> | null = null;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/profile/growth") return json(route, { summary_json: { diagnosis_count: 1, observed_points: 1, recurring_weaknesses: [], mastered_topics: [] } });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === "/api/sessions" && request.method() === "POST") return json(route, { id: SESSION_ID, title: "", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "", title_source: "auto", status: "active", updated_at: "now" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: run ? [run] : [] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: reportReady ? [{ id: "report-e2e", run_id: RUN_ID, question: "什么是 RAG？", overall_score: 8, created_at: "now" }] : [] });
    if (path === `/api/sessions/${SESSION_ID}/reports/report-e2e`) return json(route, { id: "report-e2e", run_id: RUN_ID, question: "什么是 RAG？", overall_score: 8, created_at: "now", report_markdown: "# RAG 诊断\n\n检索与生成边界清晰。" });
    if (path === "/api/coach/reports/export" && request.method() === "POST") {
      run = { id: RUN_ID, session_id: SESSION_ID, type: "report_export", status: "pending", input: request.postDataJSON() };
      return json(route, { run, reused: false }, 202);
    }
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "POST") {
      const body = request.postDataJSON();
      run = { id: RUN_ID, session_id: SESSION_ID, type: body.type, status: "pending", input: body.input, created_at: "now" };
      return json(route, { run, reused: false }, 202);
    }
    if (path === `/api/runs/${RUN_ID}/stream`) {
      const body = [event("run_started", {}, 1), event("text_delta", { content: "已收到。" }, 2), ...(reportReady || run?.type === "diagnosis" ? [event("report_ready", { report_id: "report-e2e", overall_score: 8 }, 3)] : []), event("run_completed", { success: true }, 4)].join("");
      reportReady = true;
      messages = [{ role: "user", content: "用户输入" }, { role: "assistant", content: "已收到。" }];
      if (run) run = { ...run, status: "completed" };
      return route.fulfill({ contentType: "text/event-stream", body });
    }
    if (path === `/api/runs/${RUN_ID}/cancel`) return json(route, { run: { ...(run || { id: RUN_ID }), status: "cancelled" } });
    if (path === `/api/sessions/${SESSION_ID}/audio-uploads`) return json(route, { upload: { id: "upload-e2e" } });
    if (path === `/api/sessions/${SESSION_ID}/transcript`) return json(route, { message: {} });
    return json(route, {});
  });
}

test("decodes fragmented SSE payloads and ignores heartbeats", () => {
  const decoder = new SseDecoder();
  const received: string[] = [];
  const payload = JSON.stringify({ type: "text_delta", session_id: SESSION_ID, run_id: RUN_ID, sequence: 1, created_at: "now", data: { content: "分片文本" } });
  decoder.push(": ping\n\nid: 1\ndata: " + payload.slice(0, 20), (value) => received.push(value.type));
  decoder.push(payload.slice(20) + "\n\n", (value) => received.push(value.type));
  decoder.finish((value) => received.push(value.type));
  expect(received).toEqual(["text_delta"]);
});

test("reconnects after clean EOF until a terminal event arrives", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  const received: string[] = [];
  globalThis.fetch = (async () => {
    calls += 1;
    const body = calls === 1 ? event("run_started", {}, 1) : event("run_completed", {}, 2);
    return new Response(body, { headers: { "content-type": "text/event-stream" } });
  }) as typeof fetch;
  try {
    const result = await consumeSseWithRetry(RUN_ID, 0, (value) => received.push(value.type), undefined, 2);
    expect(result).toEqual({ lastSequence: 2, terminalSeen: true });
    expect(calls).toBe(2);
    expect(received).toEqual(["run_started", "run_completed"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("reports the last sequence when EOF never reaches a terminal event", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(event("run_started", {}, 1), { headers: { "content-type": "text/event-stream" } })) as typeof fetch;
  try {
    await expect(consumeSseWithRetry(RUN_ID, 0, () => undefined, undefined, 1)).rejects.toMatchObject({
      name: "SseReconnectError",
      lastSequence: 1,
    } satisfies Partial<SseReconnectError>);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("resets session-scoped state when switching between active sessions", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") {
      return json(route, { sessions: [
        { id: "session-a", title: "A 会话", title_source: "auto", status: "active", updated_at: "2026-08-10T00:00:00Z" },
        { id: "session-b", title: "B 会话", title_source: "auto", status: "active", updated_at: "2026-08-09T00:00:00Z" },
      ], next_cursor: null });
    }
    const session = path.match(/^\/api\/sessions\/(session-a|session-b)(?:\/([^/]+))?$/);
    if (session) {
      const id = session[1];
      const child = session[2];
      if (request.method() === "GET" && !child) return json(route, { id, title: id === "session-a" ? "A 会话" : "B 会话", title_source: "auto", status: "active" });
      if (child === "messages") return json(route, { messages: [{ role: "user", content: id === "session-a" ? "A 的旧消息" : "B 的新消息" }] });
      if (child === "runs") return json(route, { runs: id === "session-a" ? [{ id: "run-a", session_id: id, type: "coach", status: "running", input: { message: "A" } }] : [] });
      if (child === "summary") return json(route, { summary_json: {} });
      if (child === "followups") return json(route, { followups: [] });
      if (child === "reports") return json(route, { reports: [] });
    }
    if (path === "/api/runs/run-a/stream") return route.fulfill({ contentType: "text/event-stream", body: event("run_started", {}, 1) });
    return json(route, {});
  });

  await page.goto("/session/session-a");
  await expect(page.getByText("A 的旧消息")).toBeVisible();
  await expect(page.getByRole("button", { name: /B 会话/ })).toBeVisible();
  await page.getByRole("button", { name: /B 会话/ }).click();
  await expect(page.getByText("B 的新消息")).toBeVisible();
  await expect(page.getByText("A 的旧消息")).toBeHidden();
  await page.getByLabel("练习输入").fill("B 的新输入");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
});

test("continues listening after fallback snapshot still reports an active run", async ({ page }) => {
  let streamCalls = 0;
  let runStatus = "running";
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "断线恢复", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: [{ id: RUN_ID, session_id: SESSION_ID, type: "coach", status: runStatus, input: { message: "恢复测试" } }] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/runs/${RUN_ID}/stream`) {
      streamCalls += 1;
      if (streamCalls >= 4) runStatus = "completed";
      return route.fulfill({ contentType: "text/event-stream", body: event(streamCalls >= 4 ? "run_completed" : "run_started", {}, streamCalls >= 4 ? 2 : 1) });
    }
    if (path === `/api/runs/${RUN_ID}/events`) return json(route, { events: [] });
    if (path === `/api/runs/${RUN_ID}`) return json(route, { id: RUN_ID, session_id: SESSION_ID, type: "coach", status: runStatus, input: { message: "恢复测试" }, last_event_sequence: runStatus === "completed" ? 2 : 1 });
    return json(route, {});
  });

  await page.goto(`/session/${SESSION_ID}`);
  await page.getByLabel("练习输入").fill("终态后可再次发送");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled({ timeout: 10_000 });
  expect(streamCalls).toBeGreaterThanOrEqual(4);
});

test("creates a run from a session draft and renders durable events", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(([key, value]) => sessionStorage.setItem(key, value), [`offerpilot-v2-draft-${SESSION_ID}`, "帮我准备 RAG 面试题"]);
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByLabel("练习输入")).toHaveValue("帮我准备 RAG 面试题");
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), `offerpilot-v2-draft-${SESSION_ID}`)).toBeNull();
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("已收到。")).toBeVisible();
});

test("consumes the current session draft key only once", async ({ page }) => {
  await mockApi(page);
  const key = `offerpilot-draft-${SESSION_ID}`;
  await page.goto(`/session/${SESSION_ID}`);
  await page.evaluate(([draftKey, value]) => sessionStorage.setItem(draftKey, value), [key, "新的草稿内容"]);
  await page.reload();
  await expect(page.getByLabel("练习输入")).toHaveValue("新的草稿内容");
  await expect.poll(() => page.evaluate((draftKey) => sessionStorage.getItem(draftKey), key)).toBeNull();
  await page.reload();
  await expect(page.getByLabel("练习输入")).toHaveValue("");
});

test("creates a session from the sidebar and enters it", async ({ page }) => {
  await mockApi(page);
  const createRequest = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/sessions" && request.method() === "POST");
  await page.goto("/");
  await page.getByRole("button", { name: "新建会话" }).click();
  await createRequest;
  await expect(page).toHaveURL(new RegExp(`/session/${SESSION_ID}$`));
  await expect(page.getByRole("heading", { name: "新的技术面试练习" })).toBeVisible();
});

test("uses the desktop three-column workspace and blocks smaller viewports", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByLabel("会话列表")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "会话详情" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "摘要" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "任务" })).toBeVisible();
  await page.setViewportSize({ width: 1279, height: 720 });
  await expect(page.getByText("OfferPilot 当前面向桌面端使用，请使用宽度不小于 1280px 的浏览器。")).toBeVisible();
  await expect(page.getByLabel("会话列表")).toBeHidden();
  await expect(page.getByRole("complementary", { name: "会话详情" })).toBeHidden();
});

test("defaults to summary when a session has no reports", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByRole("heading", { name: "会话摘要" })).toBeVisible();
  await expect(page.getByText("尚未形成目标")).toBeVisible();
});

test("creates a diagnosis run and exposes report details", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("tab", { name: "正式诊断" }).click();
  await page.getByLabel("面试题").fill("什么是 RAG？");
  await page.getByLabel("候选人回答").fill("RAG 通过检索增强生成，并持续评估召回质量。");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.getByText("正式诊断报告已生成。")).toBeVisible();
  await page.getByRole("tab", { name: "报告" }).click();
  await page.getByRole("button", { name: "什么是 RAG？" }).click();
  await expect(page.locator('aside[aria-label="会话详情"]').getByText("检索与生成边界清晰。")).toBeVisible();
  const exportRequest = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/coach/reports/export" && request.method() === "POST");
  await page.getByRole("button", { name: "导出 Markdown" }).first().click();
  await exportRequest;
});

test("keeps approval decisions single-flight and closes the dialog after sync", async ({ page }) => {
  let decisions = 0;
  let run: Record<string, unknown> = {
    id: RUN_ID,
    session_id: SESSION_ID,
    type: "coach",
    status: "waiting_approval",
    input: { message: "需要工具" },
    pending_approval: { id: "approval-e2e", run_id: RUN_ID, tool_name: "search_knowledge", risk_level: "medium", flow_kind: "coach", public_params: { question: "安全摘要" } },
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "审批测试", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: [run] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/runs/${RUN_ID}/stream`) return route.fulfill({ contentType: "text/event-stream", body: event("approval_required", { approval_id: "approval-e2e", tool_name: "search_knowledge", risk_level: "medium", flow_kind: "coach", public_params: { question: "安全摘要" } }, 1) });
    if (path === "/api/approvals/approval-e2e/decision") {
      decisions += 1;
      run = { ...run, status: "running", pending_approval: null };
      return json(route, { approval: { id: "approval-e2e", decision: "approve", decision_reused: decisions > 1 }, run });
    }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "允许并继续" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("已授权，服务端会自动恢复运行。")).toBeVisible();
  expect(decisions).toBe(1);
});

test("restores a pending approval and trace after refresh", async ({ page }) => {
  const approval = { id: "approval-refresh", run_id: RUN_ID, tool_name: "search_knowledge", risk_level: "medium", flow_kind: "coach", public_params: { question: "安全摘要" } };
  const run = { id: RUN_ID, session_id: SESSION_ID, type: "coach", status: "waiting_approval", input: { message: "需要工具" }, pending_approval: approval };
  let decisions = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "刷新恢复", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: [run] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === "/api/coach/state") return json(route, { session_id: SESSION_ID, run, trace: [JSON.parse(event("approval_required", { approval_id: approval.id, tool_name: approval.tool_name, risk_level: approval.risk_level, flow_kind: approval.flow_kind, public_params: approval.public_params }, 1).split("data: ")[1])], approval });
    if (path === `/api/runs/${RUN_ID}/stream`) return route.fulfill({ contentType: "text/event-stream", body: event("approval_required", { approval_id: approval.id, tool_name: approval.tool_name, risk_level: approval.risk_level, flow_kind: approval.flow_kind, public_params: approval.public_params }, 1) });
    if (path === `/api/approvals/${approval.id}/decision`) {
      decisions += 1;
      return json(route, { approval: { ...approval, decision: request.postDataJSON().decision }, run: { ...run, status: "cancelled", pending_approval: null } });
    }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "拒绝" }).click();
  await expect.poll(() => decisions).toBe(1);
});

test("submits a pending follow-up as a diagnosis run", async ({ page }) => {
  let createdInput: Record<string, unknown> | null = null;
  let run: Record<string, unknown> | null = null;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "追问测试", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: run ? [run] : [] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [{ id: "followup-e2e", question: "如何处理超时？", reason: "补充故障边界", status: "pending", exam_point_id: "runtime.timeout" }] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "POST") {
      createdInput = request.postDataJSON().input;
      run = { id: RUN_ID, session_id: SESSION_ID, type: "diagnosis", status: "pending", input: createdInput };
      return json(route, { run, reused: false }, 202);
    }
    if (path === `/api/runs/${RUN_ID}/stream`) {
      run = { ...(run || {}), status: "completed" };
      return route.fulfill({ contentType: "text/event-stream", body: [event("run_started", {}, 1), event("report_ready", { report_id: "report-e2e", overall_score: 7 }, 2), event("run_completed", { success: true }, 3)].join("") });
    }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("tab", { name: "追问" }).click();
  await page.getByRole("button", { name: "回答追问" }).click();
  await expect(page.getByLabel("面试题")).toHaveValue("如何处理超时？");
  await page.getByLabel("候选人回答").fill("设置截止时间并记录可重试边界。");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.getByText("正式诊断报告已生成。")).toBeVisible();
  expect(createdInput).toEqual({ question: "如何处理超时？", answer: "设置截止时间并记录可重试边界。", followup_id: "followup-e2e" });
});

test("uploads supported audio and keeps manual transcription available", async ({ page }) => {
  let transcript = "";
  let audioRunInput: Record<string, unknown> | null = null;
  let run: Record<string, unknown> | null = null;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "音频测试", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: transcript ? [{ role: "user", kind: "audio_transcript", content: transcript }] : [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: run ? [run] : [] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/sessions/${SESSION_ID}/transcript`) {
      transcript = request.postDataJSON().transcript;
      return json(route, { message: { id: 1, role: "user", content: transcript } });
    }
    if (path === `/api/sessions/${SESSION_ID}/audio-uploads`) {
      expect(request.headers()["content-type"]).toContain("multipart/form-data");
      return json(route, { upload: { id: "upload-e2e" } });
    }
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "POST") {
      audioRunInput = request.postDataJSON().input;
      run = { id: RUN_ID, session_id: SESSION_ID, type: "audio_transcription", status: "pending", input: audioRunInput };
      return json(route, { run, reused: false }, 202);
    }
    if (path === `/api/runs/${RUN_ID}/stream`) {
      run = { ...(run || {}), status: "completed" };
      return route.fulfill({ contentType: "text/event-stream", body: [event("run_started", {}, 1), event("run_completed", { success: true }, 2)].join("") });
    }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByLabel("手动转写文本").fill("这是手动转写结果。");
  await page.getByRole("button", { name: "填入诊断" }).click();
  await expect(page.getByLabel("候选人回答")).toHaveValue("这是手动转写结果。");
  await page.setInputFiles('input[aria-label="选择音频文件"]', { name: "answer.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("mock-mp3") });
  await page.getByRole("button", { name: "上传并转写" }).click();
  await expect(page.getByText("音频转写完成，可点击“填入诊断”。")).toBeVisible();
  expect(audioRunInput).toEqual({ upload_id: "upload-e2e" });
});

test("puts completed ASR text in the manual transcript editor before diagnosis confirmation", async ({ page }) => {
  let run: Record<string, unknown> | null = null;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "自动转写", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs: run ? [run] : [] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/coach/state`) return json(route, { session_id: SESSION_ID, run: null, trace: [], approval: null });
    if (path === `/api/runs/${RUN_ID}/stream`) return route.fulfill({ contentType: "text/event-stream", body: event("run_completed", { success: true }, 1) });
    return json(route, {});
  });
  run = { id: RUN_ID, session_id: SESSION_ID, type: "audio_transcription", status: "completed", result: { transcript: "ASR 自动转写内容" } };
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByLabel("手动转写文本")).toHaveValue("ASR 自动转写内容");
  await page.getByRole("tab", { name: "正式诊断" }).click();
  await expect(page.getByLabel("候选人回答")).toHaveValue("");
  await page.getByRole("button", { name: "填入诊断" }).click();
  await expect(page.getByLabel("候选人回答")).toHaveValue("ASR 自动转写内容");
});

test("restarts a failed run with its original input", async ({ page }) => {
  let rerunInput: Record<string, unknown> | null = null;
  let runs: Record<string, unknown>[] = [{ id: "failed-e2e", session_id: SESSION_ID, type: "coach", status: "failed", input: { message: "重跑这个练习" }, error_code: "llm_unavailable", created_at: "now" }];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "重跑测试", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "GET") return json(route, { runs });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs` && request.method() === "POST") {
      rerunInput = request.postDataJSON().input;
      runs = [{ id: RUN_ID, session_id: SESSION_ID, type: "coach", status: "pending", input: rerunInput }, ...runs];
      return json(route, { run: runs[0], reused: false }, 202);
    }
    if (path === `/api/runs/${RUN_ID}/stream`) {
      runs[0] = { ...runs[0], status: "completed" };
      return route.fulfill({ contentType: "text/event-stream", body: [event("run_started", {}, 1), event("run_completed", { success: true }, 2)].join("") });
    }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("tab", { name: "任务" }).click();
  await page.getByRole("button", { name: "重新运行" }).click();
  await expect.poll(() => rerunInput).toEqual({ message: "重跑这个练习" });
});

test("renames, archives and deletes a session through confirmed lifecycle actions", async ({ page }) => {
  let session = { id: SESSION_ID, title: "初始标题", title_source: "auto", status: "active", updated_at: "now" };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: session.status === "active" ? [session] : [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, session);
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "PATCH") {
      const body = request.postDataJSON();
      session = { ...session, ...(typeof body.title === "string" ? { title: body.title, title_source: "manual" } : {}), ...(typeof body.archived === "boolean" ? { status: body.archived ? "archived" : "active" } : {}) };
      return json(route, session);
    }
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "DELETE") return route.fulfill({ status: 204 });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs`) return json(route, { runs: [] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  const actionRoot = page.locator("header");
  await actionRoot.getByTitle("重命名会话").click();
  await page.getByLabel("会话标题").fill("手动标题");
  await actionRoot.getByTitle("保存标题").click();
  await expect(page.getByRole("heading", { name: "手动标题" })).toBeVisible();
  await actionRoot.getByTitle("归档会话").click();
  await expect(page.getByLabel("练习输入")).toBeDisabled();
  await actionRoot.getByTitle("删除会话").click();
  await expect(page.getByRole("dialog", { name: "删除当前会话" })).toBeVisible();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByRole("heading", { name: "新的技术面试练习" })).toBeVisible();
});

test("loads growth on its own page and exposes only safe persisted run metrics", async ({ page }) => {
  const run = {
    id: RUN_ID, session_id: SESSION_ID, type: "diagnosis", status: "completed", input: {},
    timing: { queue_duration_ms: 100, approval_wait_ms: 0, active_duration_ms: 4100, total_duration_ms: 4200 },
    metrics: {
      calls_by_type: { llm: { calls: 2, succeeded: 2, failed: 0, retries: 1 }, embedding: { calls: 0, succeeded: 0, failed: 0, retries: 0 }, asr: { calls: 0, succeeded: 0, failed: 0, retries: 0 }, tool: { calls: 0, succeeded: 0, failed: 0, retries: 0 } },
      call_count: 2, success_count: 2, failure_count: 0, retry_count: 1, total_tokens: 30,
      unknown_token_calls: 1, audio_seconds: "0", estimated_costs: { USD: "0.00003" }, cost_unknown_reasons: ["price_unknown"],
    },
  };
  const calls = [
    { id: 1, logical_call_id: "diagnosis:known", parent_call_id: "", call_type: "llm", provider: "deepseek", model: "test", operation_name: "interview_diagnosis", status: "succeeded", started_at: "now", ended_at: "now", duration_ms: 3000, attempt_count: 2, retry_count: 1, error_category: "", input_tokens: 10, output_tokens: 20, total_tokens: 30, reasoning_tokens: 0, first_token_ms: 500, audio_seconds: null, price_currency: "USD", estimated_cost: "0.00003", usage_known: true, price_known: true, unknown_reason: "" },
    { id: 2, logical_call_id: "diagnosis:unknown", parent_call_id: "", call_type: "llm", provider: "deepseek", model: "test", operation_name: "followup", status: "succeeded", started_at: "now", ended_at: "now", duration_ms: 500, attempt_count: 1, retry_count: 0, error_category: "", input_tokens: null, output_tokens: null, total_tokens: null, reasoning_tokens: null, first_token_ms: null, audio_seconds: null, price_currency: "", estimated_cost: "", usage_known: false, price_known: false, unknown_reason: "price_unknown" },
  ];
  const events = [{ type: "diagnosis_model_completed", session_id: SESSION_ID, run_id: RUN_ID, sequence: 1, created_at: "now", data: { stream_mode: "stream", duration_ms: 3000, first_token_ms: 500, input_tokens: 10, output_tokens: 20, generated_chars: 240, finish_reason: "stop" } }];
  let eventsRequested = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
  if (path === "/api/profile/growth") return json(route, { summary_json: { diagnosis_count: 3, observed_points: 12, recurring_weaknesses: ["runtime.timeout"], mastered_topics: [] } });
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    if (path === `/api/sessions/${SESSION_ID}` && request.method() === "GET") return json(route, { id: SESSION_ID, title: "详情测试", title_source: "auto", status: "active" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/runs`) return json(route, { runs: [run] });
    if (path === `/api/sessions/${SESSION_ID}/summary`) return json(route, { summary_json: {} });
    if (path === `/api/sessions/${SESSION_ID}/followups`) return json(route, { followups: [] });
    if (path === `/api/sessions/${SESSION_ID}/reports`) return json(route, { reports: [] });
    if (path === `/api/runs/${RUN_ID}`) return json(route, run);
    if (path === `/api/runs/${RUN_ID}/calls`) return json(route, { calls });
    if (path === `/api/runs/${RUN_ID}/events`) { eventsRequested = true; return json(route, { events }); }
    return json(route, {});
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("button", { name: "成长概览" }).click();
  await expect(page).toHaveURL(/\/growth$/);
  await expect(page.getByText("诊断次数")).toBeVisible();
  await expect(page.getByText("3")).toBeVisible();
  await page.goBack();
  await page.getByRole("tab", { name: "任务" }).click();
  await page.getByRole("button", { name: "查看指标" }).click();
  await expect.poll(() => eventsRequested).toBe(true);
  const runDetails = page.locator('aside[aria-label="会话详情"]');
  await expect(runDetails.getByText("预估费用：")).toBeVisible();
  await expect(runDetails.getByText("0.00003 USD，部分未知")).toBeVisible();
  await expect(runDetails.getByText("Token 未知 1")).toBeVisible();
  await runDetails.getByText("LLM · followup · succeeded").click();
  await expect(runDetails.getByText("预估费用 未知")).toBeVisible();
  await expect(runDetails.getByText("技术指标")).toBeVisible();
  await expect(runDetails.getByText("输入 Token 10").last()).toBeHidden();
  await runDetails.getByText("技术指标").click();
  await expect(runDetails.getByText("输入 Token 10")).toBeVisible();
  await expect(runDetails.getByText("输出 Token 20")).toBeVisible();
});

test("requires confirmation before profile reset", async ({ page }) => {
  let resetCalled = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/profile/bootstrap") return json(route, { profile_id: "profile-e2e" });
    if (path === "/api/profile/reset" && request.method() === "POST") {
      resetCalled = true;
      return json(route, {});
    }
    if (path === "/api/sessions" && request.method() === "GET") return json(route, { sessions: [], next_cursor: null });
    return json(route, {});
  });
  await page.goto("/");
  await page.getByRole("button", { name: "重置全部数据" }).click();
  const dialog = page.getByRole("dialog", { name: "重置全部训练数据" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "重置全部数据" }).click();
  await page.getByRole("button", { name: "确认重置" }).click();
  await expect.poll(() => resetCalled).toBe(true);
});

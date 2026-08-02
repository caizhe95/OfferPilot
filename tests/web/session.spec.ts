import { expect, test, type Page, type Route } from "@playwright/test";
import { SseDecoder } from "../../src/web/src/app/sse";

const SESSION_ID = "session-e2e";
const TRACE_ID = "trace-e2e";

type MockOptions = {
  state?: Record<string, unknown>;
  coachSse?: string;
  onRequest?: (route: Route) => Promise<boolean>;
};

type SessionState = Record<string, unknown> & { approvals: Record<string, unknown>[] };

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function event(type: string, data: Record<string, unknown>, sequence: number) {
  return `data: ${JSON.stringify({ type, session_id: SESSION_ID, trace_id: TRACE_ID, sequence, ...data })}\n\n`;
}

function defaultState(overrides: Record<string, unknown> = {}): SessionState {
  return {
    session: { id: SESSION_ID, status: "ready" },
    run: null,
    trace: null,
    approvals: [],
    ...overrides,
  };
}

async function mockApi(page: Page, options: MockOptions = {}) {
  await page.route("**/api/**", async (route) => {
    if (options.onRequest && (await options.onRequest(route))) return;
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/profile/bootstrap" && request.method() === "POST") return json(route, {});
    if (path === "/api/sessions" && request.method() === "POST") return json(route, { id: SESSION_ID });
    if (path === `/api/sessions/${SESSION_ID}`) return json(route, { id: SESSION_ID, status: "ready" });
    if (path === `/api/sessions/${SESSION_ID}/messages`) return json(route, { messages: [] });
    if (path === `/api/sessions/${SESSION_ID}/progress`) return json(route, { events: [] });
    if (path === `/api/sessions/${SESSION_ID}/checkpoints/latest`) return json(route, null, 404);
    if (path === "/api/coach/state") return json(route, options.state || defaultState());
    if (path === "/api/coach" && request.method() === "POST") {
      return route.fulfill({
        contentType: "text/event-stream",
        body:
          options.coachSse ||
          [
            event("session_start", { mode: "coach" }, 1),
            event("text_delta", { content: "已收到。" }, 2),
            event("final_response", { content: "已收到。" }, 3),
            event("run_complete", { status: "completed", success: true }, 4),
          ].join(""),
      });
    }
    if (path === "/api/coach/cancel") return json(route, { status: "cancel_requested" });
    if (path === "/api/permission/approve") return json(route, { status: "approved" });
    if (path === "/api/permission/deny") return json(route, { status: "denied" });
    if (path === "/api/coach/resume") {
      return route.fulfill({
        contentType: "text/event-stream",
        body: [event("text_delta", { content: "已继续。" }, 1), event("run_complete", { status: "completed" }, 2)].join(""),
      });
    }
    if (path === "/api/audio/upload") return json(route, { status: "transcribed", transcript: "音频转写文本", provider: "test" });
    if (path === "/api/audio/resume") return json(route, { status: "transcribed", transcript: "音频转写文本", provider: "test" });
    if (path === "/api/audio/transcript/manual") return json(route, { status: "saved", transcript: "手动文本" });
    if (path === "/api/coach/reports/export") return json(route, { report_markdown: "# 导出报告" });
    if (path === "/api/coach/reports/export/resume") return json(route, { status: "executed", report: { report_markdown: "# 导出报告" } });
    return json(route, {});
  });
}

test("decodes fragmented SSE data, heartbeats, and unknown events", () => {
  const decoder = new SseDecoder();
  const received: string[] = [];
  const payload = JSON.stringify({
    type: "text_delta",
    session_id: SESSION_ID,
    trace_id: TRACE_ID,
    sequence: 1,
    content: "分片文本",
  });
  decoder.push(": ping\n\ndata: " + payload.slice(0, 18), (event) => received.push(event.type));
  decoder.push(payload.slice(18) + "\n\ndata: {\"type\":\"unexpected\"}\n\n", (event) => received.push(event.type));
  decoder.finish((event) => received.push(event.type));
  expect(received).toEqual(["text_delta", "unknown"]);
});

test("consumes a home draft once and renders a streamed coach reply", async ({ page }) => {
  let coachBody: Record<string, unknown> | null = null;
  await mockApi(page, {
    onRequest: async (route) => {
      if (new URL(route.request().url()).pathname !== "/api/coach") return false;
      coachBody = route.request().postDataJSON() as Record<string, unknown>;
      return false;
    },
  });
  await page.addInitScript(([key, value]) => sessionStorage.setItem(key, value), [
    `offerpilot-draft-${SESSION_ID}`,
    "帮我准备 RAG 面试题",
  ]);
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByText("帮我准备 RAG 面试题")).toBeVisible();
  await expect(page.getByText("已收到。")).toBeVisible();
  await expect.poll(() => coachBody?.message).toBe("帮我准备 RAG 面试题");
  await expect(page.evaluate((key) => sessionStorage.getItem(key), `offerpilot-draft-${SESSION_ID}`)).resolves.toBeNull();
});

test("switches to formal diagnosis and exports through POST approval", async ({ page }) => {
  const diagnosisStream = [
    event("session_start", { mode: "diagnosis" }, 1),
    event("diagnosis_started", {}, 2),
    event("report_ready", { report_id: "report-e2e", overall_score: 8 }, 3),
    event("final_response", { content: "# 正式诊断\n\n结果正常。" }, 4),
    event("run_complete", { status: "completed", success: true }, 5),
  ].join("");
  let exportMethod = "";
  const state = defaultState();
  await mockApi(page, {
    state,
    coachSse: diagnosisStream,
    onRequest: async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/coach/reports/export") {
        exportMethod = route.request().method();
        state.approvals = [
          {
            request_id: "export-approval",
            tool_name: "export_report",
            risk_level: "high",
            flow_kind: "export",
            status: "pending",
            params: { report_id: "report-e2e" },
            trace_id: TRACE_ID,
          },
        ];
        await json(route, {
          type: "permission_required",
          request_id: "export-approval",
          tool_name: "export_report",
          risk_level: "high",
          params: { report_id: "report-e2e" },
        });
        return true;
      }
      if (path === "/api/permission/approve") {
        state.approvals = [
          {
            request_id: "export-approval",
            tool_name: "export_report",
            risk_level: "high",
            flow_kind: "export",
            status: "approved",
            params: { report_id: "report-e2e" },
            trace_id: TRACE_ID,
          },
        ];
        await json(route, { status: "approved" });
        return true;
      }
      if (path === "/api/coach/reports/export/resume") {
        state.approvals = [];
        await json(route, { status: "executed", report: { report_markdown: "# 导出报告" } });
        return true;
      }
      return false;
    },
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("tab", { name: "正式诊断" }).click();
  await page.getByPlaceholder("面试题").fill("什么是 RAG？");
  await page.getByPlaceholder("候选人回答").fill("RAG 结合检索与生成，并通过评估持续优化。");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.getByRole("tab", { name: "正式诊断", selected: true })).toBeVisible();
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  await expect(page.getByText("报告导出需要确认。")).toBeVisible();
  await expect.poll(() => exportMethod).toBe("POST");
  await expect(page.getByText("报告编号：report-e2e")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "允许并继续" }).click();
  expect((await download).suggestedFilename()).toContain("diagnosis-");
});

test("restores an approved approval and resumes without approving twice", async ({ page }) => {
  let approvalCalls = 0;
  let resumeCalls = 0;
  const state = defaultState({
    session: { id: SESSION_ID, status: "waiting_approval" },
    run: { trace_id: TRACE_ID, status: "waiting_approval" },
    approvals: [
      {
        request_id: "audio-approved",
        tool_name: "transcribe_audio",
        risk_level: "medium",
        flow_kind: "audio",
        status: "approved",
        params: { filename: "answer.wav", size: 1024 },
        trace_id: TRACE_ID,
      },
    ],
  });
  await mockApi(page, {
    state,
    onRequest: async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/permission/approve") approvalCalls += 1;
      if (path === "/api/audio/resume") {
        resumeCalls += 1;
        state.approvals = [];
      }
      return false;
    },
  });
  await page.goto(`/session/${SESSION_ID}`);
  await expect(page.getByRole("button", { name: "继续执行" })).toBeVisible();
  await expect(page.getByText("文件：answer.wav；大小：1.0 KB；风险：medium")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "继续执行" })).toBeVisible();
  await page.getByRole("button", { name: "继续执行" }).click();
  await expect.poll(() => resumeCalls).toBe(1);
  expect(approvalCalls).toBe(0);
});

test("keeps listening after a failed stop and only aborts after confirmation", async ({ page }) => {
  let cancelSucceeded = false;
  await mockApi(page, {
    state: defaultState({
      session: { id: SESSION_ID, status: "running" },
      run: { trace_id: TRACE_ID, status: "running" },
      trace: { id: TRACE_ID, status: "running", events: [] },
    }),
    onRequest: async (route) => {
      if (new URL(route.request().url()).pathname !== "/api/coach/cancel") return false;
      if (!cancelSucceeded) {
        await json(route, { detail: "not available" }, 409);
      } else {
        await json(route, { status: "cancel_requested" });
      }
      return true;
    },
  });
  await page.goto(`/session/${SESSION_ID}`);
  await page.getByRole("button", { name: "停止" }).click();
  await expect(page.getByText("停止请求失败：not available。仍在继续监听服务端终态。")).toBeVisible();
  cancelSucceeded = true;
  await page.getByRole("button", { name: "停止" }).click();
  await expect(page.getByText("停止请求已确认，服务端正在收尾。")).toBeVisible();
});

test("uploads audio and migrates a legacy profile id into the cookie bootstrap", async ({ page }) => {
  const bootstrapBodies: unknown[] = [];
  await mockApi(page, {
    onRequest: async (route) => {
      if (new URL(route.request().url()).pathname !== "/api/profile/bootstrap") return false;
      bootstrapBodies.push(route.request().postDataJSON());
      return false;
    },
  });
  await page.addInitScript(() => localStorage.setItem("offerpilot.profile_id", "legacy-profile"));
  await page.goto("/");
  await page.getByPlaceholder(/推荐一题/).fill("开始练习");
  await page.getByRole("button", { name: "开始本轮练习" }).click();
  await page.waitForURL(new RegExp(`/session/${SESSION_ID}$`));
  await expect.poll(() => bootstrapBodies.length).toBeGreaterThan(0);
  expect(bootstrapBodies[0]).toEqual({ legacy_profile_id: "legacy-profile" });
  await expect(page.evaluate(() => localStorage.getItem("offerpilot.profile_id"))).resolves.toBeNull();

  await page.getByLabel("选择音频文件").setInputFiles({
    name: "answer.wav",
    mimeType: "audio/wav",
    buffer: Buffer.from("RIFF----WAVEfmt "),
  });
  await page.getByRole("button", { name: "上传并请求转写" }).click();
  await expect(page.getByText("转写完成：test，6 字。")).toBeVisible();
});

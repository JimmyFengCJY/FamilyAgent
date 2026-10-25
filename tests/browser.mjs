import { chromium } from "playwright";
import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { once } from "node:events";
import { mkdtemp, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import assert from "node:assert/strict";

const root = process.cwd();
const localPython = path.join(root, ".venv312/bin/python");
const python = process.env.PYTHON_BIN || (existsSync(localPython) ? localPython : "python3");
// Refuse to attach to another test/server instance on this fixed test port.
const probe = createServer();
await new Promise((resolve, reject) => {
  probe.once("error", reject);
  probe.listen(8010, "127.0.0.1", resolve);
});
await new Promise(resolve => probe.close(resolve));
const temp = await mkdtemp(path.join(tmpdir(), "familyagent-browser-"));
const artifacts = path.join(root, "test-results");
await mkdir(artifacts, { recursive: true });
const base = "http://127.0.0.1:8010";
const server = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "services.api.app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8010",
  ],
  {
    cwd: root,
    env: {
      ...process.env,
      FAMILYAGENT_DB: path.join(temp, "qa.sqlite3"),
      FAMILYAGENT_PROVIDER: "demo",
      FAMILYAGENT_DEMO_LOGIN: "1",
      FAMILYAGENT_EMBEDDED_WORKER: "1",
      FAMILYAGENT_DEMO_DELAY: "0.25",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let serverLog = "";
let spawnError;
server.on("error", error => { spawnError = error; });
server.stderr.on("data", (data) => {
  serverLog += data;
});
let browser;
const errors = [];
let step = "startup";
try {
  let ready = false;
  for (let i = 0; i < 60; i++) {
    if (spawnError) throw spawnError;
    if (server.exitCode !== null) throw new Error("Test API exited before readiness");
    try {
      const r = await fetch(`${base}/api/health`);
      if (r.ok && (await r.json()).provider === "demo") { ready = true; break; }
    } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  if (!ready) throw new Error("Test API readiness timed out");
  const macChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const executablePath = process.env.CHROME_PATH || (!process.env.CI && existsSync(macChrome) ? macChrome : undefined);
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1050 },
    locale: "zh-CN",
    timezoneId: "Asia/Hong_Kong",
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().startsWith("Failed to load resource:")
    )
      errors.push(message.text());
  });
  page.on("response", (response) => {
    if (
      response.status() >= 400 &&
      !(response.status() === 401 && new URL(response.url()).pathname === "/api/state")
    )
      errors.push(`${response.status()} ${response.url()}`);
  });
  const clickNav = async (name) => {
    await page
      .getByRole("navigation")
      .getByRole("button", { name, exact: true })
      .click();
    if (page.viewportSize().width <= 800)
      await page.waitForFunction(
        () =>
          document.querySelector(".sidebar").getBoundingClientRect().right <= 1,
      );
  };
  const waitText = async (text) => {
    await page
      .getByText(text, { exact: false })
      .first()
      .waitFor({ timeout: 15000 });
  };
  step = "login";
  await page.goto(base);
  await page.getByRole("button", { name: "免配置，体验完整工作流" }).click();
  await waitText("你好，Jimmy");
  await page.screenshot({
    path: path.join(artifacts, "01-home.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  step = "meeting";
  await page.getByRole("button", { name: "开启一次家庭会议" }).click();
  await page.getByLabel("空间名称").fill("秋日家庭旅行");
  await page.getByLabel("持续约束", { exact: true }).fill("总预算不超过 8000 元\n不能影响孩子上课");
  assert.equal(await page.getByLabel("讨论方式").inputValue(), "independent");
  await page
    .getByLabel("议题内容")
    .fill("下个月安排一次家庭旅行，预算 8000 元，别影响孩子上课。");
  await page.getByRole("button", { name: "开始讨论", exact: true }).click();
  await waitText("讨论有了方向，决定交给你们");
  await waitText("工具权限");
  await waitText("仅用户确认后执行");
  await page.getByRole("button", { name: "检索本地 MCP 参考库", exact: true }).click();
  await waitText("MCP 连接与检索成功");
  await waitText("familyagent://reference/goal");
  await waitText("本次空间的持续约束");
  await waitText("总预算不超过 8000 元");
  assert.equal(await page.getByText("独立首轮观点", { exact: true }).count(), 3);
  await waitText("AI 共同建议 · 尚待成员确认");
  await waitText("分歧与待澄清取舍 · AI 整理");
  await waitText("这些建议是否存在资源取舍？请成员补充实际约束。");
  await page
    .getByRole("button", { name: "运行观察", exact: true })
    .last()
    .click();
  await waitText("检查点已保存");
  await waitText("上下文预算检查完成");
  await page.screenshot({
    path: path.join(artifacts, "02-meeting.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认讨论结论" }).click();
  await waitText("你已确认，等待其他成员");
  step = "private-chat";
  await clickNav("伙伴聊天");
  await page.getByRole("button", { name: "创建空间", exact: true }).click();
  await page.getByLabel("空间类型").selectOption("support");
  await page.getByLabel("空间名称").fill("Jimmy 的私密心事");
  await page.getByLabel("议题内容").fill("最近工作压力很大，想先找人聊聊。");
  await page.getByRole("button", { name: "开始讨论", exact: true }).click();
  await waitText("你希望我先听你说");
  await page.screenshot({
    path: path.join(artifacts, "03-private-chat.png"),
    fullPage: true,
  });
  step = "memory";
  await clickNav("记忆空间");
  await page.getByRole("button", { name: "保存一条记忆", exact: true }).click();
  await page.getByLabel("记忆内容").fill("每天晚上只安排 30 分钟学习。");
  await page.getByRole("button", { name: "保存这条记忆", exact: true }).click();
  await waitText("每天晚上只安排 30 分钟学习。");
  step = "member-acceptance";
  await page.getByRole("button", { name: "空间设置", exact: true }).click();
  await page.getByRole("button", { name: "切换到 林悦" }).click();
  await waitText("你好，林悦");
  await clickNav("伙伴聊天");
  assert.equal(
    await page.getByText("Jimmy 的私密心事", { exact: true }).count(),
    0,
  );
  await clickNav("记忆空间");
  assert.equal(
    await page
      .getByText("每天晚上只安排 30 分钟学习。", { exact: true })
      .count(),
    0,
  );
  await clickNav("家庭会议");
  await page.getByRole("button", { name: /秋日家庭旅行/ }).click();
  await page.getByRole("button", { name: "确认讨论结论" }).click();
  await waitText("讨论结论已确认，尚未创建任务");
  await page.getByRole("button", { name: "空间设置", exact: true }).click();
  await page.getByRole("button", { name: "切换到 Jimmy" }).click();
  await waitText("你好，Jimmy");
  step = "separate-task-approval";
  await clickNav("家庭会议");
  await page.getByRole("button", { name: /秋日家庭旅行/ }).click();
  await page
    .getByRole("button", { name: "整理为任务草案", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认并创建任务", exact: true })
    .click();
  await waitText("已确认并创建");
  step = "question-and-advice";
  await clickNav("伙伴聊天");
  await page.getByRole("button", { name: "创建空间", exact: true }).click();
  await page.getByLabel("空间名称").fill("解释与建议测试");
  await page.getByLabel("议题内容").fill("什么是上下文管理？");
  await page.getByRole("button", { name: "开始讨论", exact: true }).click();
  await waitText("这是解释问答");
  assert.equal(
    await page
      .getByRole("button", { name: "确认并创建任务", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await page
      .getByRole("button", { name: "整理为任务草案", exact: true })
      .count(),
    0,
  );
  await page.getByLabel("本条消息的处理方式").selectOption("advice");
  await page.getByLabel("输入消息").fill("给我一些整理房间的建议");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await page
    .getByRole("button", { name: "整理为任务草案", exact: true })
    .waitFor({ timeout: 15000 });
  assert.equal(
    await page
      .getByRole("button", { name: "确认并创建任务", exact: true })
      .count(),
    0,
  );
  await page.screenshot({
    path: path.join(artifacts, "09-advice-optional.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "整理为任务草案", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认并创建任务", exact: true })
    .click();
  await waitText("已确认并创建");
  step = "tasks";
  await clickNav("任务清单");
  await waitText("和家人确认可出行日期与人数");
  await page
    .getByRole("button", {
      name: "完成：和家人确认可出行日期与人数",
      exact: true,
    })
    .click();
  await page.getByRole("button", { name: "已完成", exact: true }).click();
  await waitText("和家人确认可出行日期与人数");
  await page.getByRole("button", { name: "添加任务", exact: true }).click();
  await page.getByLabel("任务标题").fill("喝水与休息");
  await page.getByLabel("提醒时间").fill("2026-10-13T08:00");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "添加任务", exact: true })
    .click();
  await page.getByRole("button", { name: "待完成", exact: true }).click();
  await waitText("喝水与休息");
  step = "planning";
  await clickNav("个人规划");
  await page.getByRole("button", { name: "创建空间", exact: true }).click();
  await page.getByLabel("空间名称").fill("三个月英语计划");
  await page.getByRole("checkbox", { name: "参考我主动保存的个人记忆" }).check();
  await page.getByLabel("议题内容").fill("帮我制定三个月英语学习计划，每天半小时。");
  await page.getByRole("button", { name: "开始讨论", exact: true }).click();
  await page
    .getByRole("button", { name: "确认并创建任务", exact: true })
    .waitFor({ timeout: 15000 });
  await page.getByText("本次输入参考记忆（1 条）", { exact: true }).click();
  await waitText("来源：用户主动保存");
  const memoryState = await (await page.request.get(`${base}/api/state`)).json();
  const sourceMemory = memoryState.memories.find((m) => m.content === "每天晚上只安排 30 分钟学习。");
  assert.ok(sourceMemory);
  assert.equal((await page.request.delete(`${base}/api/memories/${sourceMemory.id}`)).status(), 200);
  await waitText("原记忆已删除或不可访问。");
  await page
    .getByRole("button", { name: "确认并创建任务", exact: true })
    .click();
  await waitText("已确认并创建");
  await clickNav("个人规划");
  await waitText("已确认的成长计划");
  await waitText("0/3 项完成");
  step = "finance";
  await clickNav("预算分析");
  await page.getByLabel("本期收入").fill("20000");
  await page.getByRole("button", { name: "精确计算", exact: true }).click();
  await waitText("8,000.00");
  await page.screenshot({
    path: path.join(artifacts, "04-budget.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "请明理分析", exact: true }).click();
  await waitText("收入 20000.00 CNY");
  step = "custom-character";
  await clickNav("角色工作室");
  await page.getByRole("button", { name: "创造新伙伴" }).click();
  await page.getByLabel("角色名字").fill("阿岚");
  await page.getByLabel("交流风格").fill("温和但具体，每次只建议一个小步骤。");
  await page.getByLabel("角色介绍").fill("陪我坚持学习的伙伴");
  await page.getByRole("button", { name: "保存角色", exact: true }).click();
  await waitText("阿岚");
  await page.screenshot({
    path: path.join(artifacts, "05-characters.png"),
    fullPage: true,
  });
  step = "budget-recovery";
  await clickNav("家庭会议");
  await page.getByRole("button", { name: "发起会议", exact: true }).click();
  await page.getByLabel("空间名称").fill("测试调用预算与恢复");
  await page.getByRole("button", { name: "创建空间", exact: true }).click();
  await page.getByLabel("模型调用上限").selectOption("1");
  await page.getByLabel("输入消息").fill("讨论本周家庭分工");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await page.getByText("预算用完", { exact: true }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "继续", exact: true }).click();
  // Resume raises to 4 calls; five steps require one further recovery.
  await page.getByText("预算用完", { exact: true }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await waitText("讨论有了方向，决定交给你们");
  step = "observer";
  await clickNav("运行观察");
  await waitText("每一步，都有记录");
  await page.screenshot({
    path: path.join(artifacts, "06-observer.png"),
    fullPage: true,
  });
  step = "message pagination";
  const pagingResponse = await context.request.post(`${base}/api/conversations`, { data: { title: "分页合成验证", mode: "meeting" } });
  assert.equal(pagingResponse.status(), 201);
  const paging = await pagingResponse.json();
  const appendMessages = (start, end) => execFileSync(python, ["-c", `
import json,sqlite3,sys
with sqlite3.connect(sys.argv[1]) as conn:
    for i in range(int(sys.argv[3]),int(sys.argv[4])):
        obj={'id':'msg_paging_'+str(i),'conversation_id':sys.argv[2],'actor_type':'human','actor_id':'synthetic','actor_name':'分页验证','content':'分页消息 '+str(i),'created_at':'2026-10-25T00:00:00Z'}
        conn.execute('INSERT INTO messages(id,data) VALUES(?,?)',(obj['id'],json.dumps(obj)))
`, path.join(temp, "qa.sqlite3"), paging.id, String(start), String(end)]);
  appendMessages(0, 125);
  await page.reload();
  await clickNav("家庭会议");
  await page.getByRole("button", { name: /分页合成验证/ }).click();
  await page.getByText("分页消息 124", { exact: true }).waitFor();
  assert.equal(await page.locator("article.message").count(), 50);
  await page.getByRole("button", { name: "加载更早的消息", exact: true }).click();
  await page.getByText("分页消息 25", { exact: true }).waitFor();
  assert.equal(await page.locator("article.message").count(), 100);
  // Polling must preserve the already loaded older page.
  appendMessages(125, 128);
  await page.getByText("分页消息 127", { exact: true }).waitFor();
  assert.equal(await page.locator("article.message").count(), 103);
  await page.getByRole("button", { name: "加载更早的消息", exact: true }).click();
  await page.getByText("分页消息 0", { exact: true }).waitFor();
  assert.equal(await page.locator("article.message").count(), 128);
  assert.equal(await page.getByRole("button", { name: "加载更早的消息", exact: true }).count(), 0);
  await clickNav("运行观察");
  step = "mobile";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await clickNav("今天");
  await page.screenshot({
    path: path.join(artifacts, "07-mobile-home.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
    "mobile overflow",
  );
  await page.getByRole("button", { name: "开启一次家庭会议" }).click();
  assert.equal(await page.getByRole("dialog").count(), 1);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "打开导航" }).click();
  await clickNav("家庭会议");
  await page.getByRole("button", { name: /秋日家庭旅行/ }).click();
  await page
    .getByRole("button", { name: "查看协作与运行", exact: true })
    .click();
  await page.getByLabel("模型调用上限").waitFor();
  await page.getByRole("button", { name: "关闭协作面板", exact: true }).click();
  await page.screenshot({
    path: path.join(artifacts, "08-mobile-meeting.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
    "mobile conversation overflow",
  );
  assert.deepEqual(errors, [], "browser console errors");
  console.log(
    "PASS: desktop and mobile, meeting consensus, privacy, tasks, finance, character creation, budget recovery, trace.",
  );
  console.log(`Screenshots: ${artifacts}`);
} catch (error) {
  console.error(`Browser QA failed at ${step}:`, error);
  console.error(serverLog.slice(-2000));
  if (browser) {
    const pages = browser.contexts()[0]?.pages();
    if (pages?.[0])
      await pages[0].screenshot({
        path: path.join(artifacts, "failure.png"),
        fullPage: true,
      });
  }
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null && !spawnError) {
    const exited = once(server, "exit");
    server.kill("SIGTERM");
    const timer = setTimeout(() => server.kill("SIGKILL"), 5000);
    try { await exited; } finally { clearTimeout(timer); }
  }
}

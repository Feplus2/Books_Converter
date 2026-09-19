// 病例 050 补拍：滚动到队列区截图（运行中带详情行 + 完成后带真实边界刻度点）
import { writeFileSync } from "node:fs";

const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
const ws = new WebSocket(page.webSocketDebuggerUrl);
let mid = 0; const pending = new Map();
const call = (m, p) => new Promise((res) => { pending.set(++mid, res); ws.send(JSON.stringify({ id: mid, method: m, params: p })); });
ws.onmessage = (e) => { const msg = JSON.parse(e.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); } };
await new Promise((r) => (ws.onopen = r));
const evalJS = async (expr) => {
  const r = await call("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (name) => {
  const s = await call("Page.captureScreenshot", { format: "png" });
  writeFileSync(`F:/MyProjects/Books_Converter/_regress/${name}`, Buffer.from(s.data, "base64"));
  console.log("截图:", name);
};
await call("Page.enable", {});

await evalJS("location.reload()");
await sleep(3500);

// 入队 vlm 任务并开跑
await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const base = { ...smod.settingsStore.settings.defaults, outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out" };
  qmod.queueStore.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], { ...base, mode: "vlm" });
  return true;
})()`);
await evalJS(`(() => { [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('开始转换') && !e.disabled)?.click(); return true; })()`);
await sleep(1200);
// 滚到队列区
await evalJS(`(() => { const el = [...document.querySelectorAll('*')].find((e) => e.childNodes.length === 1 && e.firstChild?.nodeType === 3 && e.textContent.trim() === '转换队列'); el?.scrollIntoView({ block: 'start' }); return !!el; })()`);

// 等详情行出现（VLM 阶段细节）再拍
let got = false;
for (let i = 0; i < 20; i++) {
  await sleep(700);
  const st = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue.ts")).queueStore;
    const t = q.tasks[0];
    return { status: t.status, detail: t.detail, stageName: t.stageName, percent: t.percent };
  })()`);
  if (st.status === "running" && st.detail) {
    console.log("运行中:", JSON.stringify(st));
    await shot("case050-running.png");
    got = true;
    break;
  }
  if (st.status !== "running") break;
}
if (!got) { await shot("case050-running.png"); console.log("（未捕到 detail，仍已拍）"); }

// 等完成后拍 done（进度条 100% + 刻度点 + 引擎徽章）
for (let i = 0; i < 40; i++) {
  await sleep(1500);
  const st = await evalJS(`(async () => (await import("/src/lib/queue.ts")).queueStore.tasks[0].status)()`);
  if (st !== "running") break;
}
await sleep(600);
await shot("case050-done.png");
console.log("done");
process.exit(0);

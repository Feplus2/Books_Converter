// 病例 049 E2E-B：dev 实例（CDP 9224）全回路——程序化入队 smoke.pdf →
// 点「开始转换」→ 状态翻转/进度 → 跑完验证产物目录新布局（含根级清理）
const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
if (!page) { console.log("找不到 dev 页面"); process.exit(1); }
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

// 刷新页面：拿到当前源码（含本次改动），同时清空内存队列
await evalJS("location.reload()");
await sleep(3000);

// 程序化入队（绕开原生文件对话框），输出到 _regress/smoke-out
const enq = await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const q = qmod.queueStore, s = smod.settingsStore;
  const o = { ...s.settings.defaults, outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out" };
  q.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], o);
  return { n: q.tasks.length, mode: o.mode, formats: o.formats };
})()`);
console.log("入队:", JSON.stringify(enq));

// 点「开始转换」（走真实 UI 按钮）
const clicked = await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('开始转换') && !e.disabled); if (el) { el.click(); return true; } return false; })()`);
console.log("点击开始:", clicked);
await sleep(2500);
const flip = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  const t = q.tasks[q.tasks.length - 1];
  const card = [...document.querySelectorAll('*')].map((e) => e.childNodes.length === 1 && e.firstChild.nodeType === 3 ? e.textContent.trim() : '').filter((x) => ['待开始','进行中','完成','失败','已取消'].includes(x));
  return { status: t.status, percent: t.percent, stage: t.stage, stageName: t.stageName, 状态徽章: card };
})()`);
console.log("2.5s 后:", JSON.stringify(flip));

// 轮询至完成/失败（上限 240s；smoke.pdf 仅 2 页）
let last = null;
for (let i = 0; i < 48; i++) {
  await sleep(5000);
  last = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue.ts")).queueStore;
    const t = q.tasks[q.tasks.length - 1];
    return { status: t.status, percent: Math.round(t.percent), stage: t.stageName, logs: t.logs.slice(-2), err: t.error || null };
  })()`);
  if (i % 4 === 0 || ["done", "error", "cancelled"].includes(last.status)) console.log(`t+${(i + 1) * 5}s:`, JSON.stringify(last));
  if (["done", "error", "cancelled"].includes(last.status)) break;
}
console.log("终态:", JSON.stringify(last));
process.exit(0);

// 病例 049 E2E-B 终版：按 performance 资源表里的 ?t= URL 导入 queue.ts
// （与应用同一模块实例），点「开始转换」→ 状态翻转/进度 → 取消 → reload 清场
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

const STEP = `(async () => {
  const urls = performance.getEntriesByType('resource').map((e) => e.name).filter((n) => n.includes('/src/lib/queue.ts'));
  const appUrl = urls.find((n) => n.includes('?t=')) || urls[0];
  const q = (await import(appUrl)).queueStore;
  const surls = performance.getEntriesByType('resource').map((e) => e.name).filter((n) => n.includes('/src/lib/settings.ts'));
  const s = (await import(surls.find((n) => n.includes('?t=')) || surls[0])).settingsStore;
  return { q, s };
})()`;

// 入队（若队列里已有 smoke 且 queued 则复用）
const enq = await evalJS(`(async () => {
  const { q, s } = await ${STEP};
  let t = q.tasks.find((x) => x.title === 'smoke' && x.status === 'queued');
  if (!t) {
    const o = { ...s.settings.defaults, outputDir: 'F:/MyProjects/Books_Converter/_regress/smoke-out' };
    q.add(['F:/MyProjects/Books_Converter/_regress/smoke.pdf'], o);
    t = q.tasks[q.tasks.length - 1];
  }
  return { tasks: q.tasks.length, smoke: t.status };
})()`);
console.log("入队:", JSON.stringify(enq));

// 等按钮使能后点击
let clicked = false;
for (let i = 0; i < 20 && !clicked; i++) {
  clicked = await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('开始转换')); if (el && !el.disabled) { el.click(); return true; } return false; })()`);
  if (!clicked) await sleep(500);
}
console.log("点击开始:", clicked);
if (!clicked) process.exit(1);

let sawRunning = false;
for (let i = 0; i < 12; i++) {
  await sleep(5000);
  const s = await evalJS(`(async () => {
    const { q } = await ${STEP};
    const t = q.tasks[q.tasks.length - 1];
    return { status: t.status, percent: Math.round(t.percent), stage: t.stageName, logs: t.logs.slice(-1), err: t.error || null };
  })()`);
  console.log(`t+${(i + 1) * 5}s:`, JSON.stringify(s));
  if (s.status === "running") sawRunning = true;
  if (["done", "error", "cancelled"].includes(s.status)) break;
  if (sawRunning && i >= 2) break;
}
console.log("确认运行中翻转:", sawRunning);

const cancel = await evalJS(`(async () => {
  const { q } = await ${STEP};
  const t = q.tasks[q.tasks.length - 1];
  if (t.status === 'running' || t.status === 'queued') q.cancel(t.id);
  return t.status;
})()`);
console.log("取消后:", cancel);
await sleep(1500);
await evalJS("location.reload()");
console.log("已 reload 清场");
process.exit(0);

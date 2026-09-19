// 病例 050 E2E：dev 实例（CDP 9224）——
// a) 队列卡片引擎徽章（VLM/MinerU 两张）；b) 悬停位移已移除（.lift/.btn:hover 无 transform）；
// c) 跑 _regress/smoke.pdf（vlm 缓存）：start 事件 engine=vlm、stage_bounds 到达、
//    OCR 日志行按引擎变化、详情行随进度更新；
// d) 产物库「再次转换」toast 带引擎名。截图存 _regress/case050-*.png
import { writeFileSync } from "node:fs";

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
const shot = async (name) => {
  const s = await call("Page.captureScreenshot", { format: "png" });
  writeFileSync(`F:/MyProjects/Books_Converter/_regress/${name}`, Buffer.from(s.data, "base64"));
  console.log("截图:", name);
};
await call("Page.enable", {});
const fails = [];
const check = (name, cond, extra = "") => { console.log(`${cond ? "✓" : "✗"} ${name}${extra ? " — " + extra : ""}`); if (!cond) fails.push(name); };

// 刷新页面：拿到当前源码（含本次改动），同时清空内存队列
await evalJS("location.reload()");
await sleep(3500);

// ── b) 悬停位移移除（样式表静态断言：.lift:hover/.btn:hover 无 transform） ──
const css = await evalJS(`(() => {
  const txt = [...document.querySelectorAll('style')].map((s) => s.textContent).join('\\n');
  const grab = (sel) => { const i = txt.indexOf(sel); if (i < 0) return ''; const j = txt.indexOf('{', i); const k = txt.indexOf('}', j); return txt.slice(j, k); };
  return { lift: grab('.lift:hover'), btn: grab('.btn:hover:not(:disabled)') };
})()`);
check(".lift:hover 无 transform", !/transform/.test(css.lift), JSON.stringify(css.lift).slice(0, 120));
check(".btn:hover 无 transform，保留光晕", !/transform/.test(css.btn) && /box-shadow/.test(css.btn), JSON.stringify(css.btn).slice(0, 120));

// ── a) 入队两本（vlm + mineru），验证引擎徽章 ──
const enq = await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const q = qmod.queueStore, s = smod.settingsStore;
  const base = { ...s.settings.defaults, outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out" };
  q.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], { ...base, mode: "vlm" });
  q.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], { ...base, mode: "rule", ruleEngine: "mineru" });
  return { n: q.tasks.length, engines: q.tasks.map((t) => smod.engineOf(t.options)) };
})()`);
console.log("入队:", JSON.stringify(enq));
await sleep(800);
const badges = await evalJS(`(() => {
  const txt = document.body.innerText;
  return { VLM: txt.includes('VLM'), MinerU: txt.includes('MinerU'), 待开始: (txt.match(/待开始/g) || []).length };
})()`);
check("队列卡片显示 VLM 徽章", badges.VLM);
check("队列卡片显示 MinerU 徽章", badges.MinerU);
check("两张待开始卡片", badges.待开始 >= 2);
await shot("case050-badges.png");

// ── c) 取消 mineru 任务，只跑 vlm（缓存复用）──
await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const q = qmod.queueStore;
  const t = q.tasks.find((x) => smod.engineOf(x.options) === "mineru" && x.status === "queued");
  if (t) q.cancel(t.id);
  return true;
})()`);
const clicked = await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('开始转换') && !e.disabled); if (el) { el.click(); return true; } return false; })()`);
console.log("点击开始:", clicked);

// 运行中采样：详情行 / 阶段行 / start 事件
let sawDetail = "", sawStage = "", runningShot = false;
let last = null;
for (let i = 0; i < 60; i++) {
  await sleep(1500);
  last = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue.ts")).queueStore;
    const t = q.tasks.find((x) => x.status === "running") || q.tasks.find((x) => x.status === "done") || q.tasks[0];
    return { status: t.status, percent: Math.round(t.percent * 10) / 10, stage: t.stage,
             stageName: t.stageName, stagesTotal: t.stagesTotal, stageBounds: t.stageBounds,
             detail: t.detail, logs: t.logs, err: t.error || null };
  })()`);
  if (last.detail && !sawDetail) sawDetail = last.detail;
  if (last.stageName && !sawStage) sawStage = last.stageName;
  if (last.status === "running" && last.detail && !runningShot) { runningShot = true; await shot("case050-running.png"); }
  if (["done", "error", "cancelled"].includes(last.status)) break;
}
console.log("终态:", JSON.stringify({ ...last, logs: last?.logs?.slice(0, 8) }));

check("start 事件 engine=vlm（日志含 引擎 VLM）",
  (last?.logs || []).some((l) => l.includes("开始转换《smoke》（引擎 VLM）")),
  (last?.logs || []).find((l) => l.includes("开始转换")) || "（无 logStart 行）");
check("OCR 日志行按引擎变化（不适用+VLM 视觉解析）",
  (last?.logs || []).some((l) => l.includes("OCR: 不适用") && l.includes("VLM")),
  (last?.logs || []).find((l) => l.includes("OCR")) || "（无 OCR 行）");
check("stage_bounds 到达且非三等分", Array.isArray(last?.stageBounds) && last.stageBounds.length === 3 && last.stageBounds[0] !== 100 / 3, JSON.stringify(last?.stageBounds));
check("运行中详情行有内容（如 VLM 阅读 n/n 页）", !!sawDetail, sawDetail);
check("阶段名非空", !!sawStage, sawStage);
check("转换完成", last?.status === "done", `status=${last?.status} err=${last?.err}`);
await shot("case050-done.png");

// ── d) 产物库「再次转换」toast 带引擎名 ──
await evalJS(`(async () => { const n = (await import("/src/lib/nav.ts")); n.navigate({ page: "library" }); return true; })()`);
await sleep(1200);
const recon = await evalJS(`(async () => {
  const cards = [...document.querySelectorAll('.card')];
  const vlmCard = cards.find((c) => c.textContent.includes('smoke') && c.textContent.includes('VLM'));
  if (!vlmCard) return { found: false };
  vlmCard.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 300));
  const btn = [...vlmCard.querySelectorAll('button')].pop();
  if (!btn) return { found: true, btn: false };
  btn.click();
  return { found: true, btn: true };
})()`);
console.log("再次转换点击:", JSON.stringify(recon));
await sleep(1000);
const toast = await evalJS(`(() => {
  const t = [...document.querySelectorAll('[data-sonner-toast], [role=status]')].map((e) => e.textContent).filter(Boolean);
  return t.slice(0, 3);
})()`);
console.log("toast:", JSON.stringify(toast));
check("入队 toast 带引擎名（原选项（VLM））", toast.some((t) => t.includes("VLM") && t.includes("原选项")), JSON.stringify(toast));
await shot("case050-reconvert-toast.png");

// 清理：取消 reconvert 入队的任务，保持队列整洁
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  q.tasks.filter((t) => t.status === "queued").forEach((t) => q.cancel(t.id));
  return true;
})()`);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

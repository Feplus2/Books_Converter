// 病例 054 E2E：dev 实例（CDP 9224）——失败任务的「重试」按钮
// a) 合成失败任务：不存在路径的 PDF 入队 + startAll → 子进程报「PDF 文件不存在」
//    → 卡片置 error（顺带走过 pipeline 失败音接线点）
// b) 卡片出现「重试」图标按钮（icon-btn，aria-label=重试）
// c) 点击重试 → toast「已重新入队」+ 状态回 queued → pump 接力重新起跑
//    （PDF 仍不存在 → 再次 error），全程不新建卡片
// d) 重置后错误文本/进度清零
// 截图存 _regress/case054-*.png
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

// ── 门禁：落在转换页 + settings 加载完成 ──
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "convert" }); return true; })()`);
let ready = false;
for (let i = 0; i < 24 && !ready; i++) {
  await sleep(500);
  ready = await evalJS(`document.body.innerText.includes('拖入 PDF')`);
}
if (!ready) { console.log("未落在转换页"); process.exit(1); }
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.loaded)()`)) break;
}

// ── a) 合成失败任务（不存在的 PDF）──
const BAD_PDF = "D:/no/such/case054-ghost.pdf";
const setup = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  const s = await import("/src/lib/settings.ts");
  const opts = { ...s.defaultConvertOptions(), formats: ["epub"] };
  q.add(["${BAD_PDF}"], opts);
  const t = q.tasks[q.tasks.length - 1];
  void q.startAll();
  return { id: t.id, hasKey: !!s.settingsStore.settings.ocr.mineruToken.trim() };
})()`);
console.log("合成任务 id:", setup.id, "（预检 key 在场:", setup.hasKey, "）");

// 等子进程失败落 error（Python 启动 + 报错退出，留足 90s）
let st = "";
for (let i = 0; i < 90; i++) {
  await sleep(1000);
  st = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue.ts")).queueStore;
    return q.tasks.find((t) => t.id === "${setup.id}")?.status ?? "gone";
  })()`);
  if (st === "error" || st === "gone") break;
}
check("a1 不存在 PDF 的任务转 error", st === "error", `status=${st}`);
const errInfo = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  const t = q.tasks.find((t) => t.id === "${setup.id}");
  return { error: t?.error ?? "", n: q.tasks.filter((x) => x.pdfPath === "${BAD_PDF}").length };
})()`);
check("a2 错误文案在场（人话标题）", !!errInfo.error, errInfo.error);
check("a3 只有一张卡片（不新建）", errInfo.n === 1);
await shot("case054-error-card.png");

// ── b) 重试按钮出现 ──
const btnState = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift")].find((c) => c.textContent.includes("case054-ghost"));
  if (!card) return { card: false };
  const btn = card.querySelector('button.icon-btn[aria-label="重试"]');
  if (btn) btn.scrollIntoView({ block: "center" });
  return { card: true, btn: !!btn, errBadge: card.textContent.includes("失败") };
})()`);
check("b1 失败卡片在场且有「重试」icon-btn", btnState.card && btnState.btn);
check("b2 失败徽标在场", btnState.errBadge);
await shot("case054-retry-button.png");

// ── c) 点击重试：toast + 状态翻转 queued → 接力重跑 → 再 error ──
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift")].find((c) => c.textContent.includes("case054-ghost"));
  card.querySelector('button.icon-btn[aria-label="重试"]').click();
  return true;
})()`);
await sleep(600);
const afterClick = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  const t = q.tasks.find((t) => t.id === "${setup.id}");
  return {
    status: t?.status,
    error: t?.error ?? null,
    percent: t?.percent,
    toast: document.body.innerText.includes("已重新入队"),
    n: q.tasks.filter((x) => x.pdfPath === "${BAD_PDF}").length,
  };
})()`);
check("c1 点击后回 queued/running（交 pump 重跑）", afterClick.status === "queued" || afterClick.status === "running", `status=${afterClick.status}`);
check("c2 错误/进度已清零", afterClick.error === null && afterClick.percent === 0);
check("c3 toast「已重新入队」可见", afterClick.toast);
check("c4 仍只有一张卡片（原位重跑）", afterClick.n === 1);
await shot("case054-retried-running.png");

// 等第二次失败落定（证明确实重跑了，而不是卡在 queued）
let st2 = "";
for (let i = 0; i < 90; i++) {
  await sleep(1000);
  st2 = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue.ts")).queueStore;
    return q.tasks.find((t) => t.id === "${setup.id}")?.status ?? "gone";
  })()`);
  if (st2 === "error" || st2 === "gone") break;
}
check("c5 重跑后再次落 error（真重跑，非假翻转）", st2 === "error", `status=${st2}`);
await shot("case054-retried-error.png");

// ── 收尾：清掉合成任务卡片 ──
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  q.clearFinished();
  return q.tasks.length;
})()`);
await sleep(500);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

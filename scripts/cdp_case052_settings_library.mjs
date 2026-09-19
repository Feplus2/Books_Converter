// 病例 052 E2E：dev 实例（CDP 9224）——多实例配置分叉 / 产物库去污 / 删除记录与忽略名单 / startAll 汇总
// a) dev/正式配置分叉：dev 写 settings.dev.json，release settings.json 全程不动（哈希比对）；
//    且 dev 首跑一次性继承正式配置（密钥/提供商在）
// b) 去污：产物库不再显示 temp_files 未登记文档、不再显示 smoke-out（historyDirs 被 prune）
// c) 聚焦重载：外部改 settings.dev.json + window focus → 内存更新；坏 JSON → 保持内存
// d) 删除记录：合成 registry 卡片「删除记录」→ 记录消失、产物文件原地不动
// e) 未登记「不再显示」→ 消失且刷新（reload）后不复现（ignoredPaths 落盘）；chips 恢复
// f) startAll toast 引擎汇总「即将开始 2 个任务：MinerU×1、VLM×1」
// g) addHistoryDir 拒绝仓库内路径
// 截图存 _regress/case052-*.png
import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";

const APPDATA = process.env.APPDATA;
const REL_CFG = `${APPDATA}/com.booksconverter.app/settings.json`;
const DEV_CFG = `${APPDATA}/com.booksconverter.app/settings.dev.json`;
const REG_DIR = "F:/MyProjects/Books_Converter/_regress/case052-reg";
const SCAN_DIR = "F:/MyProjects/Books_Converter/_regress/case052-scan";
const REG_FILE = `${REG_DIR}/_registry.jsonl`;
const REG_PRODUCT = `${REG_DIR}/case052-product.epub`;
const SCAN_FILE = `${SCAN_DIR}/fake-unregistered.epub`;
const relHash = () => createHash("sha256").update(readFileSync(REL_CFG)).digest("hex").slice(0, 16);
const relHashBefore = relHash();

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

// ── 门禁：reload 后必须落在转换页（新实例 + 最新前端代码） ──
await evalJS("location.reload()");
let ready = false;
for (let i = 0; i < 24 && !ready; i++) {
  await sleep(500);
  ready = await evalJS(`document.body.innerText.includes('拖入 PDF')`);
}
if (!ready) { console.log("reload 后未落在转换页"); process.exit(1); }
// 等 settings load 完成（含 prune）
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.loaded)()`)) break;
}

// ── a) dev 分叉：触发一次保存 → settings.dev.json 落盘；release 文件不动 ──
await evalJS(`(async () => {
  const s = (await import("/src/lib/settings.ts")).settingsStore;
  s.update({}); // 无变化补丁，仅触发保存
  return true;
})()`);
await sleep(800);
check("a1 settings.dev.json 已落盘（dev 独立配置文件）", existsSync(DEV_CFG));
check("a2 release settings.json 未被 dev 触碰", relHash() === relHashBefore, `${relHashBefore} → ${relHash()}`);
const inherit = await evalJS(`(async () => {
  const s = (await import("/src/lib/settings.ts")).settingsStore.settings;
  return { vlm: s.defaults.vlmModel, hasKey: s.providers.some((p) => p.apiKey), outputDir: s.defaults.outputDir };
})()`);
check("a3 dev 首跑一次性继承正式配置（vlmModel/key/outputDir 在）",
  inherit.vlm === "deepseek/deepseek-flash" && inherit.hasKey && inherit.outputDir.replace(/\\/g, "/") === "D:/temp_files",
  JSON.stringify(inherit));

// ── b) 去污：historyDirs  prune + 扫描只吃 scanDirs ──
const prune = await evalJS(`(async () => {
  const s = (await import("/src/lib/settings.ts")).settingsStore.settings;
  return { history: s.historyDirs, scan: s.scanDirs };
})()`);
check("b1 historyDirs 已剔除仓库内 smoke-out", !prune.history.some((d) => d.includes("_regress")), JSON.stringify(prune.history));
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "library" }); return true; })()`);
await sleep(1500);
const libText = await evalJS(`document.body.innerText`);
check("b2 产物库无 smoke-out 登记记录", !libText.includes("smoke") && !libText.includes("case051"));
check("b3 产物库无未登记条目（scanDirs 为空 → 不扫 temp_files）", !libText.includes("未登记"));
await shot("case052-library-clean.png");

// ── g) addHistoryDir 拒绝仓库内路径 ──
const histGuard = await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  m.settingsStore.addHistoryDir("F:\\\\MyProjects\\\\Books_Converter\\\\_regress\\\\smoke-out");
  const after = m.settingsStore.settings.historyDirs;
  m.settingsStore.addHistoryDir("D:/case052-normal-dir");
  const ok2 = m.settingsStore.settings.historyDirs.includes("D:/case052-normal-dir");
  return { blocked: !after.some((d) => d.includes("_regress")), added: ok2 };
})()`);
check("g1 addHistoryDir 拒绝仓库内 _regress 路径", histGuard.blocked);
check("g2 addHistoryDir 正常目录照常入历史", histGuard.added);

// ── c) 聚焦重载 ──
// 外部（另一实例视角）改写 sound，focus → 内存应跟随
await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  window.__before = m.settingsStore.settings.sound;
  return true;
})()`);
{
  const cur = JSON.parse(readFileSync(DEV_CFG, "utf8"));
  cur.sound = !cur.sound;
  // 保证 mtime 推进（自己保存的 lastWriteAt 之后）
  await sleep(1100);
  writeFileSync(DEV_CFG, JSON.stringify(cur, null, 2));
}
await evalJS(`window.dispatchEvent(new Event("focus"))`);
await sleep(700);
const flipped = await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  return { before: window.__before, after: m.settingsStore.settings.sound };
})()`);
check("c1 外部改写 + focus → 内存重载", flipped.before !== flipped.after, JSON.stringify(flipped));
// 坏 JSON → 保持内存
await sleep(100);
writeFileSync(DEV_CFG, "{broken json");
await sleep(1100);
await evalJS(`window.dispatchEvent(new Event("focus"))`);
await sleep(700);
const afterBroken = await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.settings.sound)()`);
check("c2 坏 JSON + focus → 保持内存不动作", afterBroken === flipped.after);
// 恢复正常（改回继承内容，供后续用例用）
{
  const cur = JSON.parse(readFileSync(REL_CFG, "utf8"));
  cur.historyDirs = []; // dev 文件：写回已 prune 的历史
  await sleep(100);
  writeFileSync(DEV_CFG, JSON.stringify(cur, null, 2));
}
await evalJS(`window.dispatchEvent(new Event("focus"))`);
await sleep(700);

// ── e) 未登记「不再显示」 ──
await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  const s = m.settingsStore.settings;
  m.settingsStore.update({ scanDirs: ["${SCAN_DIR}"] });
  return true;
})()`);
await sleep(1500); // settings 变化 → refresh
const unregVisible = await evalJS(`document.body.innerText.includes("fake-unregistered")`);
check("e1 scanDir 内未登记文件出现", unregVisible);
await shot("case052-unregistered.png");
// 点「不再显示」（group-hover 覆盖层按钮：直接 DOM click，悬停态由 CDP 鼠标补充）
const ignored = await evalJS(`(() => {
  const cards = [...document.querySelectorAll(".card.lift.group")];
  const card = cards.find((c) => c.textContent.includes("fake-unregistered"));
  if (!card) return "card-not-found";
  const btns = [...card.querySelectorAll("button.icon-btn")];
  if (!btns.length) return "btn-not-found";
  btns[0].click();
  return "clicked";
})()`);
await sleep(1200);
const afterIgnore = await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  return {
    gone: !document.body.innerText.includes("badge") && ![...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("fake-unregistered")),
    ignored: m.settingsStore.settings.ignoredPaths,
    chips: document.body.innerText.includes("已忽略"),
  };
})()`);
check("e2 「不再显示」后未登记卡片消失", ignored === "clicked" && afterIgnore.gone, ignored);
check("e3 ignoredPaths 已写入内存设置", afterIgnore.ignored.some((p) => p.includes("fake-unregistered")), JSON.stringify(afterIgnore.ignored));
check("e4 「已忽略」chips 行出现", afterIgnore.chips);
await sleep(600);
check("e5 ignoredPaths 落盘 settings.dev.json",
  existsSync(DEV_CFG) && readFileSync(DEV_CFG, "utf8").includes("fake-unregistered"));
await shot("case052-ignored.png");
// reload 后不复现
await evalJS("location.reload()");
for (let i = 0; i < 24; i++) {
  await sleep(500);
  if (await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.loaded)()`)) break;
}
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "library" }); return true; })()`);
await sleep(1500);
const afterReload = await evalJS(`(() => ({
  unreg: [...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("fake-unregistered")),
  chips: document.body.innerText.includes("已忽略"),
}))()`);
check("e6 reload 后忽略条目不复现（chips 仍在，可恢复）", !afterReload.unreg && afterReload.chips, JSON.stringify(afterReload));
// chips 点 X 恢复 → 卡片回来
await evalJS(`(() => {
  const chip = [...document.querySelectorAll("span")].find((s) => s.textContent.includes("fake-unregistered") && s.className.includes("card"));
  const x = chip?.querySelector("button");
  if (x) x.click();
  return !!x;
})()`);
await sleep(1200);
const restored = await evalJS(`[...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("fake-unregistered"))`);
check("e7 chips 移除后未登记卡片恢复显示", restored);

// ── d) 已登记「删除记录」：记录消失、产物文件原地不动 ──
await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  const s = m.settingsStore.settings;
  // 直接 update 注入（addHistoryDir 会拒绝仓库内路径——那是守卫，不是数据通道）
  m.settingsStore.update({ historyDirs: ["${REG_DIR}", ...s.historyDirs] });
  return true;
})()`);
await sleep(1500);
const regVisible = await evalJS(`[...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("case052合成书"))`);
check("d1 合成登记记录出现在产物库", regVisible);
// 悬停卡片 → 点删除钮（第二个 icon-btn；第一个是再次转换）
const regCard = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case052合成书"));
  if (!card) return null;
  card.scrollIntoView({ block: "center" });
  const r = card.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()`);
await call("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.round(regCard.x + regCard.w / 2), y: Math.round(regCard.y + regCard.h / 2) });
await sleep(400);
await shot("case052-regcard-hover.png");
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case052合成书"));
  const btns = [...card.querySelectorAll("button.icon-btn")];
  btns[btns.length - 1].click(); // ListX
  return true;
})()`);
await sleep(400);
await shot("case052-regcard-confirm.png");
const confirmText = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case052合成书"));
  return card ? card.textContent : "";
})()`);
check("d2 卡片两段确认文案写明「只删记录，不删产物文件」", confirmText.includes("不删产物文件"), confirmText.slice(0, 80));
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case052合成书"));
  [...card.querySelectorAll("button")].find((b) => b.textContent.trim() === "确认")?.click();
  return true;
})()`);
await sleep(1200);
const afterRemove = await evalJS(`[...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("case052合成书"))`);
check("d3 确认后记录从产物库消失", !afterRemove);
check("d4 registry 文件已无该 ts", !readFileSync(REG_FILE, "utf8").includes("2026-02-01T10:00:00"));
check("d5 产物文件原地不动", existsSync(REG_PRODUCT));

// ── f) startAll toast 引擎汇总 ──
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "convert" }); return true; })()`);
await sleep(800);
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  const s = (await import("/src/lib/settings.ts")).settingsStore;
  const base = { ...s.settings.defaults, outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out" };
  q.add(["F:/nonexistent/case052-a.pdf"], { ...base, mode: "rule", ruleEngine: "mineru" });
  q.add(["F:/nonexistent/case052-b.pdf"], { ...base, mode: "vlm" });
  return true;
})()`);
await sleep(500);
await evalJS(`(() => {
  const el = [...document.querySelectorAll("button")].find((e) => (e.textContent || "").includes("开始转换") && !e.disabled);
  el.click();
  return true;
})()`);
await sleep(800);
const toastText = await evalJS(`[...document.querySelectorAll("[data-sonner-toast]")].map((t) => t.textContent).join(" || ")`);
check("f1 startAll toast 引擎汇总可见", toastText.includes("即将开始 2 个任务：MinerU×1、VLM×1"), toastText.slice(0, 140));
await shot("case052-startall-toast.png");
// 清理：取消/清掉假任务（PDF 不存在会快速 error，无所谓；取消 queued 的即可）
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  for (const t of [...q.tasks]) {
    if (t.status === "queued" || t.status === "running") q.cancel(t.id);
  }
  return true;
})()`);
await sleep(500);

check("a4 全程结束 release settings.json 仍未被触碰", relHash() === relHashBefore, `${relHashBefore} → ${relHash()}`);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

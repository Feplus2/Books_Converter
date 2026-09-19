// 病例 053 E2E：dev 实例（CDP 9224）——产物库纯登记制（扫描/忽略/重新定位链路已移除）
// a) 后端命令面：scan_unregistered / relocate_entry 已移除（invoke 报错）；
//    read_registry 只回登记行（同目录未登记的 stray epub 不出现）
// b) 产物库页面：无「未登记」文本/徽章、无「已忽略」/「扫描目录」chips、无添加扫描目录按钮；
//    每张卡片都有引擎徽章（登记卡特征）——D:\temp_files 本机文档彻底消失
// c) 合成登记：注入 historyDirs → 合成书/丢失书出现；stray 未登记 epub 不出现；
//    丢失书带「丢失」徽标、合成书不带
// d) 丢失书详情：丢失徽标在、无「重新定位」按钮（用户挪位置=用户行为，不追踪）
// e) 删除记录仍工作：两段确认 → 卡片消失、registry 行删、产物文件原地不动
// f) 旧配置兼容：外部写入含 scanDirs/ignoredPaths 的 settings.dev.json + focus
//    → 应用不炸、内存设置无这两字段、产物库仍纯登记
// 截图存 _regress/case053-*.png
import { writeFileSync, readFileSync, existsSync, readdirSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const APPDATA = process.env.APPDATA;
const DEV_CFG = `${APPDATA}/com.booksconverter.app/settings.dev.json`;
const REG_DIR = "F:/MyProjects/Books_Converter/_regress/case053-reg";
const REG_FILE = `${REG_DIR}/_registry.jsonl`;
const REG_PRODUCT = `${REG_DIR}/case053-product.epub`;
const TEMP_DIR = "D:/temp_files";

// ── 合成夹具（幂等：可重复跑）——两条登记行（一存在一丢失）+ 一个未登记 stray ──
mkdirSync(REG_DIR, { recursive: true });
writeFileSync(REG_PRODUCT, "fake epub content");
writeFileSync(`${REG_DIR}/case053-stray.epub`, "stray unregistered doc");
writeFileSync(REG_FILE, [
  JSON.stringify({ v: 1, ts: "2026-02-02T10:00:00+0800", title: "case053合成书", dir_name: "case053合成书", source_pdf: "F:/nonexistent/case053.pdf", work_dir: `${REG_DIR}/work`, engine: "vlm", ocr: true, translate: null, formats: ["epub"], products: { epub: [REG_PRODUCT] }, elapsed_s: 12.3, app_version: "1.3.9" }),
  JSON.stringify({ v: 1, ts: "2026-02-03T10:00:00+0800", title: "case053丢失书", dir_name: "case053丢失书", source_pdf: "F:/nonexistent/case053b.pdf", work_dir: `${REG_DIR}/work2`, engine: "mineru", ocr: true, translate: "zh", formats: ["epub"], products: { epub: [`${REG_DIR}/case053-gone.epub`] }, elapsed_s: 45.6, app_version: "1.3.9" }),
].join("\n") + "\n");

// D:\temp_files 里会被旧扫描扫进来的本机文档（供 b 组断言点名核对）
function listDocs(root, depth = 3) {
  const out = [];
  const walk = (d, n) => {
    if (n < 0) return;
    let ents;
    try { ents = readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p, n - 1);
      else if (/\.(epub|md|tex)$/i.test(e.name)) out.push(p);
    }
  };
  walk(root, depth);
  return out;
}
const tempDocs = listDocs(TEMP_DIR);
console.log(`temp_files 下 epub/md/tex 文档 ${tempDocs.length} 个（旧扫描的污染源）`);

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

// ── 门禁：reload 后落在转换页 + settings 加载完成 ──
const devCfgSnapshot = existsSync(DEV_CFG) ? readFileSync(DEV_CFG, "utf8") : null;
await evalJS("location.reload()");
let ready = false;
for (let i = 0; i < 24 && !ready; i++) {
  await sleep(500);
  ready = await evalJS(`document.body.innerText.includes('拖入 PDF')`);
}
if (!ready) { console.log("reload 后未落在转换页"); process.exit(1); }
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.loaded)()`)) break;
}

// ── a) 后端命令面 ──
const gone = await evalJS(`(async () => {
  const inv = window.__TAURI_INTERNALS__.invoke;
  const tryCmd = async (cmd, args) => { try { await inv(cmd, args); return "STILL-EXISTS"; } catch (e) { return String(e); } };
  return {
    scan: await tryCmd("scan_unregistered", { dirs: [], exclude: [] }),
    relocate: await tryCmd("relocate_entry", { registryPath: "x", ts: "x", fmt: "epub", oldPath: "a", newPath: "b" }),
    read: await inv("read_registry", { dirs: ["${REG_DIR}"] }),
  };
})()`);
check("a1 scan_unregistered 命令已移除", gone.scan.includes("not found"), gone.scan);
check("a2 relocate_entry 命令已移除", gone.relocate.includes("not found"), gone.relocate);
check("a3 read_registry 只回登记行（2 条，stray 未登记 epub 不可见）",
  Array.isArray(gone.read) && gone.read.length === 2 &&
  !JSON.stringify(gone.read).includes("stray"),
  `items=${gone.read?.length}`);
const goneEntry = Array.isArray(gone.read) ? gone.read.find((i) => i.title === "case053丢失书") : null;
check("a4 丢失书记录 missing=true（徽标数据）", goneEntry?.missing === true);

// ── b) 产物库页面纯登记制 ──
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "library" }); return true; })()`);
await sleep(1500);
const libState = await evalJS(`(() => {
  const txt = document.body.innerText;
  const cards = [...document.querySelectorAll(".card.lift.group")];
  return {
    hasUnreg: txt.includes("未登记"),
    hasIgnoredChips: txt.includes("已忽略"),
    hasScanChips: txt.includes("扫描目录"),
    allCardsRegistered: cards.every((c) => /MinerU|PaddleOCR|VLM/.test(c.textContent)),
    cardCount: cards.length,
    tempLeak: ${JSON.stringify(tempDocs.map((p) => p.split(/[\\/]/).pop()))}.filter((n) => txt.includes(n.replace(/\\.(epub|md|tex)$/i, ""))),
  };
})()`);
check("b1 无「未登记」区块/徽章", !libState.hasUnreg);
check("b2 无「已忽略」chips", !libState.hasIgnoredChips);
check("b3 无「扫描目录」chips/入口", !libState.hasScanChips);
check("b4 全部卡片都是登记卡（引擎徽章）", libState.allCardsRegistered, `cards=${libState.cardCount}`);
check("b5 temp_files 本机文档零泄漏进产物库", libState.tempLeak.length === 0, libState.tempLeak.join(","));
await shot("case053-library-clean.png");

// ── c) 合成登记记录 ──
await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  const s = m.settingsStore.settings;
  // 直接 update 注入（addHistoryDir 会拒绝仓库内路径——那是守卫，不是数据通道）
  m.settingsStore.update({ historyDirs: ["${REG_DIR}", ...s.historyDirs] });
  return true;
})()`);
await sleep(1500);
const regState = await evalJS(`(() => {
  const cards = [...document.querySelectorAll(".card.lift.group")];
  const find = (t) => cards.find((c) => c.textContent.includes(t));
  return {
    ok: !!find("case053合成书"),
    gone: !!find("case053丢失书"),
    goneBadge: find("case053丢失书")?.textContent.includes("丢失") ?? false,
    okBadge: find("case053合成书")?.textContent.includes("丢失") ?? true,
    stray: !!find("case053-stray"),
  };
})()`);
check("c1 合成登记记录出现（合成书+丢失书）", regState.ok && regState.gone);
check("c2 同目录未登记 stray epub 不出现（纯登记制）", !regState.stray);
check("c3 丢失书带「丢失」徽标、合成书不带", regState.goneBadge && !regState.okBadge);
await shot("case053-registry-only.png");

// ── d) 丢失书详情：无「重新定位」 ──
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case053丢失书"));
  card.click();
  return true;
})()`);
await sleep(900);
const detailState = await evalJS(`(() => {
  const txt = document.body.innerText;
  return {
    missingBadge: txt.includes("丢失"),
    relocate: txt.includes("重新定位"),
    goneFileRow: txt.includes("case053-gone.epub"),
  };
})()`);
check("d1 详情页丢失徽标+丢失文件行正常显示", detailState.missingBadge && detailState.goneFileRow);
check("d2 详情页无「重新定位」（不追踪用户挪动）", !detailState.relocate);
await shot("case053-detail-missing.png");
await evalJS(`(() => { [...document.querySelectorAll("button")].find((b) => b.textContent.includes("返回产物库"))?.click(); return true; })()`);
await sleep(900);

// ── e) 删除记录仍工作 ──
const regCardRect = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case053合成书"));
  if (!card) return null;
  card.scrollIntoView({ block: "center" });
  const r = card.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()`);
await call("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.round(regCardRect.x + regCardRect.w / 2), y: Math.round(regCardRect.y + regCardRect.h / 2) });
await sleep(400);
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case053合成书"));
  const btns = [...card.querySelectorAll("button.icon-btn")];
  btns[btns.length - 1].click(); // ListX 删除记录
  return true;
})()`);
await sleep(400);
await shot("case053-remove-confirm.png");
const confirmText = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case053合成书"));
  return card ? card.textContent : "";
})()`);
check("e1 两段确认文案写明「只删记录，不删产物文件」", confirmText.includes("不删产物文件"), confirmText.slice(0, 80));
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift.group")].find((c) => c.textContent.includes("case053合成书"));
  [...card.querySelectorAll("button")].find((b) => b.textContent.trim() === "确认")?.click();
  return true;
})()`);
await sleep(1200);
const afterRemove = await evalJS(`[...document.querySelectorAll(".card.lift.group")].some((c) => c.textContent.includes("case053合成书"))`);
check("e2 确认后记录从产物库消失", !afterRemove);
check("e3 registry 文件已无该 ts（丢失书行保留）", (() => {
  const c = readFileSync(REG_FILE, "utf8");
  return !c.includes("2026-02-02T10:00:00") && c.includes("2026-02-03T10:00:00");
})());
check("e4 产物文件原地不动", existsSync(REG_PRODUCT));
await shot("case053-after-remove.png");

// ── f) 旧配置兼容：含 scanDirs/ignoredPaths 的 settings.dev.json 加载不炸 ──
{
  const cur = JSON.parse(readFileSync(DEV_CFG, "utf8"));
  cur.scanDirs = ["D:/temp_files"];
  cur.ignoredPaths = ["D:/temp_files/某文档.epub"];
  await sleep(1100); // 保证 mtime 推进过 lastWriteAt
  writeFileSync(DEV_CFG, JSON.stringify(cur, null, 2));
}
await evalJS(`window.dispatchEvent(new Event("focus"))`);
await sleep(900);
const legacy = await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  const s = m.settingsStore.settings;
  const txt = document.body.innerText;
  return {
    alive: !!document.querySelector("input"),
    noScanDirs: !("scanDirs" in s),
    noIgnored: !("ignoredPaths" in s),
    stillNoUnreg: !txt.includes("未登记"),
  };
})()`);
check("f1 旧字段加载不炸（应用存活、页面正常）", legacy.alive);
check("f2 内存设置已剔除 scanDirs/ignoredPaths", legacy.noScanDirs && legacy.noIgnored);
check("f3 即便旧配置写了 scanDirs=D:/temp_files，产物库仍无未登记", legacy.stillNoUnreg);

// ── 收尾：还原 settings.dev.json（去掉注入的 historyDirs/旧字段） ──
if (devCfgSnapshot) {
  await sleep(1100);
  writeFileSync(DEV_CFG, devCfgSnapshot);
  await evalJS(`window.dispatchEvent(new Event("focus"))`);
  await sleep(900);
  const restored = await evalJS(`(async () => {
    const m = await import("/src/lib/settings.ts");
    return m.settingsStore.settings.historyDirs.some((d) => d.includes("case053"));
  })()`);
  check("g1 settings.dev.json 已还原（case053 目录不再挂历史）", !restored);
}
await shot("case053-final.png");

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

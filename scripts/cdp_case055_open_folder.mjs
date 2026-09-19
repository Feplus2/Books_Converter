// 病例 055 E2E：dev 实例（CDP 9224）——done 卡片「打开 EPUB」→「打开文件夹」
// a) 真跑 _regress/smoke.pdf（VLM，vlm_state.db 缓存秒完）→ done
// b) done 卡片按钮文案 =「打开文件夹」（无「打开 EPUB」残留）
// c) task.productDir 指向实际交付目录（撞名避让 smoke (N) 也算对）
// d) 点击按钮 → open_file 开目录：无报错通知、Explorer 进程数增加
// 截图 _regress/case055-*.png
import { writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

const SMOKE_PDF = "F:/MyProjects/Books_Converter/_regress/smoke.pdf";
const OUT_DIR = "F:/MyProjects/Books_Converter/_regress/smoke-out";

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

// ── 门禁：转换页 + settings 加载完成 + VLM 已配置 ──
await evalJS(`(async () => { (await import("/src/lib/nav")).navigate({ page: "convert" }); return true; })()`);
let ready = false;
for (let i = 0; i < 24 && !ready; i++) {
  await sleep(500);
  ready = await evalJS(`document.body.innerText.includes('拖入 PDF')`);
}
if (!ready) { console.log("未落在转换页"); process.exit(1); }
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import("/src/lib/settings")).settingsStore.loaded)()`)) break;
}

// ── a) 真跑 smoke.pdf（VLM 缓存秒完）──
const setup = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue")).queueStore;
  const s = await import("/src/lib/settings");
  const st = s.settingsStore.settings;
  const opts = { ...s.defaultConvertOptions(), mode: "vlm",
                 vlmModel: st.defaults.vlmModel, outputDir: "${OUT_DIR}",
                 formats: ["epub"] };
  q.add(["${SMOKE_PDF}"], opts);
  const t = q.tasks[q.tasks.length - 1];
  void q.startAll();
  return { id: t.id, vlmModel: st.defaults.vlmModel };
})()`);
console.log("任务 id:", setup.id, "vlmModel:", setup.vlmModel);

let st = "";
for (let i = 0; i < 300; i++) {
  await sleep(1000);
  st = await evalJS(`(async () => {
    const q = (await import("/src/lib/queue")).queueStore;
    const t = q.tasks.find((t) => t.id === "${setup.id}");
    return t ? t.status + "|" + (t.error ?? "") : "gone";
  })()`);
  if (st.startsWith("done") || st.startsWith("error") || st === "gone") break;
}
check("a1 smoke.pdf 转换 done", st.startsWith("done"), st);

// ── b/c) 卡片与 productDir ──
const cardState = await evalJS(`(async () => {
  const q = (await import("/src/lib/queue")).queueStore;
  const t = q.tasks.find((t) => t.id === "${setup.id}");
  const card = [...document.querySelectorAll(".card.lift")].find((c) => c.textContent.includes("smoke"));
  if (!card) return { card: false };
  card.scrollIntoView({ block: "center" });
  const btn = [...card.querySelectorAll("button")].find((b) => b.textContent.includes("打开"));
  return {
    card: true,
    btnText: btn?.textContent?.trim() ?? null,
    noOldText: !card.textContent.includes("打开 EPUB"),
    productDir: t?.productDir ?? null,
    epubPath: t?.epubPath ?? null,
  };
})()`);
check("b1 done 卡片按钮文案 = 打开文件夹", cardState.btnText === "打开文件夹", cardState.btnText ?? "按钮不存在");
check("b2 无「打开 EPUB」残留", cardState.noOldText);
check("c1 productDir 指向交付目录（smoke-out\\smoke 或撞名避让 smoke (N)）",
  !!cardState.productDir && /^F:\\MyProjects\\Books_Converter\\_regress\\smoke-out\\smoke( \(\d+\))?$/.test(cardState.productDir),
  cardState.productDir ?? "null");
console.log("  productDir:", cardState.productDir, "| epubPath:", cardState.epubPath);
await shot("case055-done-card.png");

// ── d) 点击「打开文件夹」→ Explorer 新窗口标题出现产物文件夹名 ──
// （__TAURI_INTERNALS__.invoke 不可写不可配，无法间谍；cmd /c start 开目录
//   复用现有 explorer 进程、进程数不变——窗口标题差集是全链路证据：
//   点击 → invoke → Rust open_file → cmd start → Explorer 窗口）
const winTitles = () => {
  try {
    return execSync(
      'powershell -NoProfile -Command "Get-Process explorer -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle} | Select-Object -ExpandProperty MainWindowTitle"',
      { encoding: "utf8" },
    ).split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  } catch { return []; }
};
const tb = winTitles();
await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift")].find((c) => c.textContent.includes("smoke"));
  [...card.querySelectorAll("button")].find((b) => b.textContent.includes("打开文件夹"))?.click();
  return true;
})()`);
await sleep(3000);
const dirBase = (cardState.productDir ?? "").split("\\").pop();
const ta = winTitles();
const newWins = ta.filter((t) => !tb.includes(t));
check("d1 点击后 Explorer 出现产物文件夹窗口", !!dirBase && newWins.some((t) => t.includes(dirBase)),
  `新增窗口: ${newWins.join(" | ") || "(无)"}（期望含 ${dirBase}）`);
const errToast = await evalJS(`(() => {
  const txt = document.body.innerText;
  return txt.includes("路径不存在") || txt.includes("打开文件失败");
})()`);
check("d2 无报错通知/提示", !errToast);
await shot("case055-after-open.png");

// ── 收尾：清掉 done 卡片 ──
await evalJS(`(async () => { (await import("/src/lib/queue")).queueStore.clearFinished(); return true; })()`);
await sleep(400);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

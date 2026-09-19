// 病例 057 E2E：dev 实例（CDP 9224）——产物库详情页按钮直义化
// a) 秦汉史 (1) 详情页：md 行（目录）只有 1 个按钮（open），无 reveal；
//    epub/tex 行（文件）双按钮
// b) tooltip：md 行 open=「打开文件夹」；epub 行 open=「打开文件」、
//    reveal=「在文件夹显示」
// c) 点 md 行 open → Explorer 真实弹出 md 目录窗口（open_file 目录分支，
//    055 修复）
// d) 点 epub 行 reveal → Explorer 弹出 epub/ 目录窗口并选中 epub 文件
//    （reveal 文件分支 /select）
// 截图 _regress/case057-*.png
import { writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

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
const winTitles = () => {
  try {
    return execSync(
      'powershell -NoProfile -Command "Get-Process explorer -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle} | Select-Object -ExpandProperty MainWindowTitle"',
      { encoding: "utf8" },
    ).split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  } catch { return []; }
};

// ── 清场：关掉残留的 Explorer 文件夹窗口（前几轮探针/E2E 开的，
//    不关会让窗口差集失效——同目录复开只聚焦不新增）；只关文件夹窗口，
//    不动桌面 shell ──
execSync(
  'powershell -NoProfile -Command "for ($i=0; $i -lt 6; $i++) { $w = (New-Object -ComObject Shell.Application).Windows(); foreach ($x in $w) { try { $x.Quit() } catch {} }; Start-Sleep -Milliseconds 700 }"',
  { encoding: "utf8" },
);
await sleep(1200);

// ── 门禁：进产物库 ──
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "library" }); return true; })()`);
let ready = false;
for (let i = 0; i < 24 && !ready; i++) {
  await sleep(500);
  ready = await evalJS(`document.body.innerText.includes('产物库')`);
}
if (!ready) { console.log("未落在产物库"); process.exit(1); }
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import("/src/lib/settings.ts")).settingsStore.loaded)()`)) break;
}
await sleep(1200);

// ── a) 找秦汉史 (1) 条目并进详情页（两个同名片段：按 products 路径含 " (1)" 区分）──
const cards = await evalJS(`(() =>
  [...document.querySelectorAll(".card.lift.group")].map((c) => c.textContent.slice(0, 40)))()`);
console.log("产物库卡片:", JSON.stringify(cards));
// 依次点开秦汉史卡片，详情里路径含 " (1)" 的即目标
let opened = false;
const nCards = cards.filter((t) => t.includes("秦汉史")).length;
for (let i = 0; i < nCards && !opened; i++) {
  await evalJS(`(() => {
    const cs = [...document.querySelectorAll(".card.lift.group")].filter((c) => c.textContent.includes("秦汉史"));
    cs[${i}]?.click();
    return true;
  })()`);
  await sleep(1000);
  opened = await evalJS(`document.body.innerText.includes(" (1)\\\\")`);
  if (!opened) {
    await evalJS(`(() => { [...document.querySelectorAll("button")].find((b) => b.textContent.includes("返回产物库"))?.click(); return true; })()`);
    await sleep(800);
  }
}
check("a1 秦汉史 (1) 详情页打开", opened);
await shot("case057-detail.png");

// ── a/b) 行级按钮数与 tooltip ──
const rows = await evalJS(`(() => {
  const rows = [...document.querySelectorAll(".card.lift")].filter((c) => c.querySelector(".mono"));
  return rows.map((r) => ({
    text: r.textContent.slice(0, 30),
    nBtns: r.querySelectorAll("button.icon-btn").length,
  }));
})()`);
console.log("文件行:", JSON.stringify(rows));
const mdRow = rows.find((r) => /md/i.test(r.text));
const epubRow = rows.find((r) => /epub/i.test(r.text));
check("a2 md 行（目录）只有 1 个按钮（无 reveal）", mdRow?.nBtns === 1, JSON.stringify(mdRow));
check("a3 epub 行（文件）2 个按钮（open+reveal）", epubRow?.nBtns === 2, JSON.stringify(epubRow));

// hover 出 tooltip（React onMouseEnter ← mouseover 冒泡合成）
const tooltips = await evalJS(`(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rows = [...document.querySelectorAll(".card.lift")].filter((c) => c.querySelector(".mono"));
  const out = {};
  for (const [key, r] of [["md", rows.find((x) => /md/i.test(x.textContent))],
                          ["epub", rows.find((x) => /epub/i.test(x.textContent))]]) {
    if (!r) continue;
    const btns = [...r.querySelectorAll("button.icon-btn")];
    for (const [bi, b] of btns.entries()) {
      b.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      await sleep(250);
      const tip = [...document.querySelectorAll(".pointer-events-none.fixed")]
        .map((e) => e.textContent).filter(Boolean).pop() ?? null;
      out[key + ":" + bi] = tip;
      b.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
      await sleep(150);
    }
  }
  return out;
})()`);
console.log("tooltips:", JSON.stringify(tooltips));
check("b1 md 行 open tooltip = 打开文件夹", tooltips["md:0"] === "打开文件夹", tooltips["md:0"] ?? "无");
check("b2 epub 行 open tooltip = 打开文件", tooltips["epub:0"] === "打开文件", tooltips["epub:0"] ?? "无");
check("b3 epub 行 reveal tooltip = 在文件夹显示", tooltips["epub:1"] === "在文件夹显示", tooltips["epub:1"] ?? "无");

// ── c) 点 md 行 open → Explorer 弹出 md 目录窗口 ──
const tb1 = winTitles();
await evalJS(`(() => {
  const rows = [...document.querySelectorAll(".card.lift")].filter((c) => c.querySelector(".mono"));
  const md = rows.find((x) => /md/i.test(x.textContent));
  md.querySelector("button.icon-btn").click();
  return true;
})()`);
await sleep(3500);
const newAfterMd = winTitles().filter((t) => !tb1.includes(t));
check("c1 md 行 open 后 Explorer 弹出 md 目录窗口", newAfterMd.some((t) => /^md( - |$)/.test(t) || t.includes("md")),
  `新增窗口: ${newAfterMd.join(" | ") || "(无)"}`);
await shot("case057-md-opened.png");

// ── d) 点 epub 行 reveal → Explorer 弹出 epub/ 窗口（/select 选中文件）──
const tb2 = winTitles();
await evalJS(`(() => {
  const rows = [...document.querySelectorAll(".card.lift")].filter((c) => c.querySelector(".mono"));
  const epub = rows.find((x) => /epub/i.test(x.textContent));
  epub.querySelectorAll("button.icon-btn")[1].click(); // reveal
  return true;
})()`);
await sleep(3500);
const newAfterEpub = winTitles().filter((t) => !tb2.includes(t));
check("d1 epub 行 reveal 后 Explorer 弹出 epub 目录窗口", newAfterEpub.some((t) => /^epub( - |$)/.test(t) || t.includes("epub")),
  `新增窗口: ${newAfterEpub.join(" | ") || "(无)"}`);
await shot("case057-epub-revealed.png");

// ── 收尾：返回产物库 ──
await evalJS(`(() => { [...document.querySelectorAll("button")].find((b) => b.textContent.includes("返回产物库"))?.click(); return true; })()`);
await sleep(600);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

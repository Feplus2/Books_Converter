// 病例 051 E2E：dev 实例（CDP 9224）全 GUI 悬停「零版面位移」验证 ——
// a) 服役 CSS 静态断言：所有 :hover/:focus 规则块无 transform/布局属性（与 vitest 守卫同源）；
// b) 真实鼠标悬停（Input.dispatchMouseEvent）前后 getBoundingClientRect 必须逐像素一致：
//    队列卡片(.lift) + 卡片按钮区(.icon-btn) + 「选择」按钮(.btn) + 导航折叠键 + 导航项；
// c) 050 复现几何：光标停在队列卡片下边缘内 1px，采样 5×100ms 断言矩形不抖（自激不复燃）；
// d) 产物库卡片 group-hover 动作钮显现（absolute 覆盖层）时卡片矩形不变（有卡片则测）。
// 截图存 _regress/case051-*.png
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

// 刷新拿当前源码，同时清空内存队列；轮询确认落在转换页
// （run 2 教训：活窗口可能被真实导航干扰，先断言页面身份再开测）
await evalJS("location.reload()");
let onConvert = false;
for (let i = 0; i < 16 && !onConvert; i++) {
  await sleep(500);
  onConvert = await evalJS(`document.body.innerText.includes('拖入 PDF')`);
}
if (!onConvert) { console.log("reload 后未落在转换页（活窗口被干扰？）"); process.exit(1); }

// ── a) 服役 CSS 静态断言（正则扫描 <style> 文本，与 vitest 守卫同口径） ──
const cssAudit = await evalJS(`(() => {
  const txt = [...document.querySelectorAll('style')].map((s) => s.textContent).join('\\n');
  const noComments = txt.replace(/\\/\\*[\\s\\S]*?\\*\\//g, '');
  const blocks = [];
  const stack = []; let buf = '';
  for (const ch of noComments) {
    if (ch === '{') { stack.push(buf.trim()); buf = ''; }
    else if (ch === '}') { const name = stack.pop() ?? ''; const body = buf.trim(); if (body) blocks.push({ sel: [...stack, name].join(' '), body }); buf = ''; }
    else buf += ch;
  }
  const stateRe = /:(hover|focus|focus-visible|focus-within|active|checked|disabled|enabled)\\b/;
  const banned = /^(transform|translate|scale|rotate|width|height|min-width|min-height|max-width|max-height|padding|padding-.+|margin|margin-.+|top|left|right|bottom|inset|position|display|content|font-size|font-weight|line-height|letter-spacing|gap|flex|flex-.+|border|border-width|border-top|border-right|border-bottom|border-left)$/;
  const hits = [];
  let n = 0;
  for (const b of blocks) {
    if (!stateRe.test(b.sel)) continue;
    n++;
    for (const d of b.body.split(';')) {
      const prop = (d.split(':')[0] || '').trim().toLowerCase();
      if (prop && banned.test(prop)) hits.push(b.sel + ' { ' + prop + ' }');
    }
  }
  return { stateBlocks: n, hits };
})()`);
check("服役 CSS 状态伪类块 ≥5（解析生效）", cssAudit.stateBlocks >= 5, `n=${cssAudit.stateBlocks}`);
check("服役 CSS 状态规则零禁属性", cssAudit.hits.length === 0, cssAudit.hits.join(" / ") || "clean");

// ── 准备：入队一本假任务让队列卡片存在（不启动，结束即取消） ──
await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const base = { ...smod.settingsStore.settings.defaults, outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out" };
  qmod.queueStore.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], { ...base, mode: "vlm" });
  return true;
})()`);
await sleep(600);

// ── b) 悬停前后矩形一致性 ──
const rectOf = (selExpr) => `(() => {
  const el = ${selExpr};
  if (!el) return null;
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()`;
const near = (a, b) => a && b && Math.abs(a.x - b.x) < 0.6 && Math.abs(a.y - b.y) < 0.6 && Math.abs(a.w - b.w) < 0.6 && Math.abs(a.h - b.h) < 0.6;
const hoverAt = async (x, y) => { await call("Input.dispatchMouseEvent", { type: "mouseMoved", x, y }); };

// 目标清单：队列卡片(.lift)、卡片按钮区展开钮(.icon-btn)、输出目录「选择」(.btn)、导航折叠键、导航项
const targets = [
  { name: "队列卡片 .lift", expr: `document.querySelector('.card.lift')` },
  { name: "队列卡片展开日志钮 .icon-btn", expr: `document.querySelector('.card.lift .icon-btn')` },
  { name: "「选择」输出目录 .btn", expr: `[...document.querySelectorAll('button.btn')].find((e) => e.textContent.includes('选择'))` },
  { name: "导航折叠键", expr: `document.querySelector('nav .icon-btn')` },
  { name: "导航项「产物库」", expr: `[...document.querySelectorAll('nav button')].find((e) => e.textContent.includes('产物库'))` },
];

for (const t of targets) {
  const before = await evalJS(rectOf(t.expr));
  if (!before) { check(`${t.name} 存在`, false, "选择器未命中"); continue; }
  await hoverAt(before.x + before.w / 2, before.y + before.h / 2);
  await sleep(300); // 越过 150ms 过渡
  const after = await evalJS(rectOf(t.expr));
  check(`${t.name} 悬停前后矩形一致`, near(before, after), `before=${JSON.stringify(before)} after=${JSON.stringify(after)}`);
  await sleep(150);
}
await shot("case051-convert-hover.png");

// 悬停确实生效的 sanity：先把鼠标移回队列卡片，再读 .lift 的 box-shadow
const cardRc = await evalJS(rectOf(`document.querySelector('.card.lift')`));
await hoverAt(cardRc.x + cardRc.w / 2, cardRc.y + cardRc.h / 2);
await sleep(300);
const glowOk = await evalJS(`(() => {
  const el = document.querySelector('.card.lift');
  if (!el) return { found: false };
  const cs = getComputedStyle(el);
  return { found: true, shadow: cs.boxShadow };
})()`);
check("悬停光晕确实生效（非空 box-shadow）", !!glowOk.found && glowOk.shadow !== "none", String(glowOk.shadow).slice(0, 90));
await shot("case051-queue-card-hover.png");

// ── c) 050 复现几何：卡片下边缘内 1px 悬停，矩形采样 5 次不抖 ──
const card = await evalJS(rectOf(`document.querySelector('.card.lift')`));
await hoverAt(card.x + card.w / 2, card.y + card.h - 1);
const samples = [];
for (let i = 0; i < 5; i++) {
  await sleep(100);
  samples.push(await evalJS(rectOf(`document.querySelector('.card.lift')`)));
}
const stable = samples.every((s) => near(s, card));
check("卡片下边缘悬停 500ms 矩形零抖动（050 自激不复燃）", stable, samples.map((s) => `y=${s.y.toFixed(1)}`).join(" "));
await shot("case051-queue-edge-hover.png");

// ── 导航悬停截图 ──
const navBtn = await evalJS(rectOf(`[...document.querySelectorAll('nav button')].find((e) => e.textContent.includes('产物库'))`));
await hoverAt(20, 20); // 移开
await sleep(250);
await shot("case051-nav-before.png");
await hoverAt(navBtn.x + navBtn.w / 2, navBtn.y + navBtn.h / 2);
await sleep(300);
await shot("case051-nav-hover.png");

// ── d) 产物库卡片 group-hover（有卡片才测） ──
await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "library" }); return true; })()`);
await sleep(1200);
const libCard = await evalJS(rectOf(`document.querySelector('.card.lift.group')`));
if (libCard) {
  await hoverAt(libCard.x + libCard.w / 2, libCard.y + libCard.h / 2);
  await sleep(300);
  const after = await evalJS(rectOf(`document.querySelector('.card.lift.group')`));
  const actionVisible = await evalJS(`(() => {
    const a = document.querySelector('.card.lift.group .group-hover\\\\:opacity-100');
    return a ? getComputedStyle(a).opacity : 'missing';
  })()`);
  check("产物库卡片悬停前后矩形一致（group-hover 动作钮为 absolute 覆盖层）", near(libCard, after), `before=${JSON.stringify(libCard)} after=${JSON.stringify(after)}`);
  check("group-hover 动作钮已显现（opacity=1）", actionVisible === "1", `opacity=${actionVisible}`);
  await shot("case051-library-card-hover.png");
} else {
  console.log("（产物库为空，跳过卡片悬停用例）");
}

// 清理：取消假任务，鼠标移开
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue.ts")).queueStore;
  q.tasks.filter((t) => t.status === "queued").forEach((t) => q.cancel(t.id));
  return true;
})()`);
await hoverAt(20, 20);

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

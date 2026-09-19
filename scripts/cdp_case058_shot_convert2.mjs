// README 配图终版：reload 清模块图 → 按页面资源表解析 vite 真实模块 URL
// （HMR 后带 ?t= 版本号，写死路径会 import 到孤儿实例）→ 合成 done 卡片截图
import { writeFileSync, mkdirSync } from "node:fs";

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
mkdirSync("F:/MyProjects/Books_Converter/docs/images", { recursive: true });
await call("Page.enable", {});

// vite 真实模块 URL（页面资源表里带 ?t= 的那个才是 App 在用的实例）
const modUrl = async (name) => {
  const u = await evalJS(`performance.getEntriesByType("resource").map((e) => e.name)
    .filter((n) => n.includes("/src/lib/" + ${JSON.stringify(name)}))
    .sort((a, b) => b.length - a.length)[0] ?? null`);
  if (!u) throw new Error(`模块 ${name} 未在页面资源表里`);
  return u.split("?")[0].includes("localhost") ? u : u; // 保留 ?t= 查询
};

await call("Page.reload", { ignoreCache: true });
await sleep(4500);
for (let i = 0; i < 20; i++) {
  if (await evalJS(`document.body.innerText.includes('拖入 PDF')`)) break;
  await sleep(500);
}
const NAV = await modUrl("nav");
const QUEUE = await modUrl("queue");
const SETTINGS = await modUrl("settings");
await evalJS(`(async () => { (await import(${JSON.stringify(NAV)})).navigate({ page: "convert" }); return true; })()`);
await sleep(800);
for (let i = 0; i < 20; i++) {
  await sleep(300);
  if (await evalJS(`(async () => (await import(${JSON.stringify(SETTINGS)})).settingsStore.loaded)()`)) break;
}

const ok = await evalJS(`(async () => {
  const q = (await import(${JSON.stringify(QUEUE)})).queueStore;
  const s = await import(${JSON.stringify(SETTINGS)});
  q.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"],
        { ...s.defaultConvertOptions(), mode: "vlm",
          vlmModel: s.settingsStore.settings.defaults.vlmModel,
          outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out",
          formats: ["epub", "md", "tex"] });
  const t = q.tasks.at(-1);
  q.onEvent(t, { type: "start", title: "smoke", engine: "vlm",
                 stage_bounds: [30.0, 92.0, 100.0] });
  q.onEvent(t, { type: "progress", stage: 3, stage_name: "EPUB 生成",
                 detail: "导出 TeX（orig）…", fraction: null, percent: 96.4 });
  q.onEvent(t, { type: "done",
                 epub_path: "F:\\\\MyProjects\\\\Books_Converter\\\\_regress\\\\smoke-out\\\\smoke (11)\\\\epub\\\\smoke.epub",
                 title: "smoke", elapsed: 2.5, percent: 100.0,
                 product_dir: "F:\\\\MyProjects\\\\Books_Converter\\\\_regress\\\\smoke-out\\\\smoke (11)" });
  return q.tasks.at(-1)?.status;
})()`);
console.log("合成状态:", ok);
await sleep(700);
const scrollInfo = await evalJS(`(() => {
  const card = [...document.querySelectorAll(".card.lift")].find((c) => c.textContent.includes("smoke"));
  if (!card) return { found: false };
  card.classList.remove("queue-flash");
  card.scrollIntoView({ block: "end", behavior: "auto" });
  return { found: true, top: Math.round(card.getBoundingClientRect().top) };
})()`);
console.log("滚动:", JSON.stringify(scrollInfo));
await sleep(500);
const s = await call("Page.captureScreenshot", { format: "png" });
writeFileSync("F:/MyProjects/Books_Converter/docs/images/convert.png", Buffer.from(s.data, "base64"));
console.log("截图: convert.png");
await evalJS(`(async () => { (await import(${JSON.stringify(QUEUE)})).queueStore.clearFinished(); return true; })()`);
process.exit(0);

// README 配图截图（病例 058）：清队列 → 真跑 smoke.pdf → 转换页 done 卡片
// + 产物库页，各截一张存 docs/images/
import { writeFileSync, mkdirSync } from "node:fs";

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
mkdirSync("F:/MyProjects/Books_Converter/docs/images", { recursive: true });
const shot = async (name) => {
  const s = await call("Page.captureScreenshot", { format: "png" });
  writeFileSync(`F:/MyProjects/Books_Converter/docs/images/${name}`, Buffer.from(s.data, "base64"));
  console.log("截图:", name);
};
await call("Page.enable", {});

// ① 清队列残留 → 真跑一本 smoke.pdf（VLM 缓存秒完），留 done 卡片
await evalJS(`(async () => { (await import("/src/lib/nav")).navigate({ page: "convert" }); return true; })()`);
await sleep(1000);
await evalJS(`(async () => {
  const q = (await import("/src/lib/queue")).queueStore;
  q.clearFinished();
  const s = await import("/src/lib/settings");
  const st = s.settingsStore.settings;
  const opts = { ...s.defaultConvertOptions(), mode: "vlm",
                 vlmModel: st.defaults.vlmModel,
                 outputDir: "F:/MyProjects/Books_Converter/_regress/smoke-out",
                 formats: ["epub"] };
  q.add(["F:/MyProjects/Books_Converter/_regress/smoke.pdf"], opts);
  void q.startAll();
  return true;
})()`);
for (let i = 0; i < 120; i++) {
  await sleep(1000);
  const st = await evalJS(`(async () => (await import("/src/lib/queue")).queueStore.tasks.at(-1)?.status)()`);
  if (st === "done" || st === "error") { console.log("转换状态:", st); break; }
}
await sleep(800);
await evalJS(`(() => { document.querySelector(".queue-flash")?.classList.remove("queue-flash"); return true; })()`);
await shot("convert.png");

// ② 产物库页
await evalJS(`(async () => { (await import("/src/lib/nav")).navigate({ page: "library" }); return true; })()`);
await sleep(1500);
await shot("library.png");
process.exit(0);

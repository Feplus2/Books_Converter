// 把 Books_Converter 产出的 EPUB 导入 SageRead dev 实例（CDP 9223，范例 cdp-library-import-new5.mjs 改造）。
// 用法: node scripts/vlm_lab/import_sageread.mjs <epub绝对路径> [更多路径...]
// 前置: dev 实例以 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9223 pnpm dev 启动。
import { copyFileSync, mkdirSync } from "node:fs";
import { basename, join } from "node:path";

const APPDATA_DIR = `${process.env.APPDATA}\\com.bettersageread.dev\\converter\\_regress-import`;
const files = process.argv.slice(2);
if (!files.length) { console.error("用法: node import_sageread.mjs <epub路径>..."); process.exit(1); }

// ① 拷进 appData（plugin-fs 权限域内）
mkdirSync(APPDATA_DIR, { recursive: true });
const staged = files.map((f) => {
  const dst = join(APPDATA_DIR, basename(f));
  copyFileSync(f, dst);
  return dst;
});
console.log("已暂存:", staged.map((s) => basename(s)).join(", "));

// ② CDP 注入 importConvertedEpub
const list = await (await fetch("http://127.0.0.1:9223/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1420"));
if (!page) { console.error("找不到 dev 页面（localhost:1420），dev 实例未就绪"); process.exit(1); }
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
await evalJS(`(async () => { window.__cs = await import("/src/services/converter-service.ts"); window.__lib = await import("/src/store/library-store.ts"); window.__bs = await import("/src/services/book-service.ts"); })()`);
for (const f of staged) {
  const r = await evalJS(`window.__cs.importConvertedEpub(${JSON.stringify(f)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`);
  console.log(`导入 ${basename(f)}: ${r}`);
}
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
const after = await evalJS(`window.__bs.getBooks()`);
console.log("当前在库:", after.length);
process.exit(0);

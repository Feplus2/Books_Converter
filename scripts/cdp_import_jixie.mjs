// jixie（机械设计手册）EPUB 导入 dev 阅读器 + VLM 标签 + 入队向量化
const list = await (await fetch("http://127.0.0.1:9223/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1420"));
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
await evalJS(`(async () => {
  window.__cs = await import("/src/services/converter-service.ts");
  window.__bs = await import("/src/services/book-service.ts");
  window.__ts = await import("/src/services/tag-service.ts");
  window.__lib = await import("/src/store/library-store.ts");
})()`);
const before = await evalJS(`window.__bs.getBooks().then((bs) => bs.map((b) => b.id))`);
const path = "C:\\Users\\20995\\AppData\\Roaming\\com.bettersageread.dev\\converter\\_v2-review\\机械设计手册 第六版 单行本 润滑与密封.epub";
console.log("导入:", await evalJS(`window.__cs.importConvertedEpub(${JSON.stringify(path)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`));
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
const after = await evalJS(`window.__bs.getBooks()`);
const nb = after.filter((b) => !before.includes(b.id));
for (const b of nb) {
  const tag = await window.__ts ? null : null;
}
const tagId = await evalJS(`window.__ts.getTagByName("VLM").then((t) => t.id)`);
for (const b of nb) {
  await evalJS(`window.__bs.updateBook(${JSON.stringify(b.id)}, { tags: ${JSON.stringify([tagId])} })`);
  console.log(`VLM → ${(b.title || "").slice(0, 40)} [${b.id.slice(0, 8)}]`);
}
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
process.exit(0);

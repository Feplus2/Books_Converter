// jixie 补标 VLM + 入队向量化（导入已完成）
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
  window.__bs = await import("/src/services/book-service.ts");
  window.__ts = await import("/src/services/tag-service.ts");
  window.__bv = await import("/src/services/task-executors/book-vectorize.ts");
})()`);
const tagId = await evalJS(`window.__ts.getTagByName("VLM").then((t) => t.id)`);
const books = await evalJS(`window.__bs.getBooks()`);
const b = books.find((x) => (x.title || "").includes("机械设计手册"));
if (!b) { console.log("未找到机械设计手册"); process.exit(1); }
await evalJS(`window.__bs.updateBook(${JSON.stringify(b.id)}, { tags: ${JSON.stringify([tagId])} })`);
console.log(`VLM → ${(b.title || "").slice(0, 40)} [${b.id.slice(0, 8)}]`);
const ok = await evalJS(`window.__bv.enqueueBookVectorize({ id: ${JSON.stringify(b.id)}, title: ${JSON.stringify(b.title || "")}, solo: true }).ok`);
console.log("向量化入队:", ok);
process.exit(0);

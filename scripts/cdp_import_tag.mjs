// 通用导入+打标工具：node cdp_import_tag.mjs <epub绝对路径> <标签名> [--vectorize]
// 依赖 dev 实例运行中（CDP 9223 + vite 1420）。
const epubPath = process.argv[2];
const tagName = process.argv[3] || "VLM";
const doVectorize = process.argv.includes("--vectorize");
if (!epubPath) { console.log("用法: node cdp_import_tag.mjs <epub路径> <标签名> [--vectorize]"); process.exit(2); }

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

// fs 权限域：仅 appdata 内可读——先复制到 converter/_import-tmp/
const fs = await import("node:fs");
const pathMod = await import("node:path");
const tmpDir = "C:\\Users\\20995\\AppData\\Roaming\\com.bettersageread.dev\\converter\\_import-tmp";
fs.mkdirSync(tmpDir, { recursive: true });
const staged = pathMod.join(tmpDir, pathMod.basename(epubPath));
fs.copyFileSync(epubPath, staged);

await evalJS(`(async () => {
  window.__cs = await import("/src/services/converter-service.ts");
  window.__bs = await import("/src/services/book-service.ts");
  window.__ts = await import("/src/services/tag-service.ts");
  window.__lib = await import("/src/store/library-store.ts");
})()`);
const tagId = await evalJS(`(async () => {
  const t = await window.__ts.getTagByName(${JSON.stringify(tagName)});
  if (t) return t.id;
  return (await window.__ts.createTag({ name: ${JSON.stringify(tagName)}, color: "#22c55e" })).id;
})()`);
const before = await evalJS(`window.__bs.getBooks().then((bs) => bs.map((b) => b.id))`);
const r = await evalJS(`window.__cs.importConvertedEpub(${JSON.stringify(staged)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`);
console.log("导入:", r);
if (r !== "ok") process.exit(1);
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
const after = await evalJS(`window.__bs.getBooks()`);
const nb = after.filter((b) => !before.includes(b.id));
for (const b of nb) {
  await evalJS(`window.__bs.updateBook(${JSON.stringify(b.id)}, { tags: ${JSON.stringify([tagId])} })`);
  console.log(`${tagName} → ${(b.title || "").slice(0, 40)} [${b.id.slice(0, 8)}]`);
  if (doVectorize) {
    await evalJS(`(async () => { const bv = await import("/src/services/task-executors/book-vectorize.ts"); bv.enqueueBookVectorize({ id: ${JSON.stringify(b.id)}, title: ${JSON.stringify(b.title || "")}, solo: true }); })()`);
    console.log("向量化入队 ✓");
  }
}
process.exit(0);

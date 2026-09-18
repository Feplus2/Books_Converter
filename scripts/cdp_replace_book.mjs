// 替换式再导入：旧条目（无进度）移回收站 → 新 EPUB 导入 + 打标 + 向量化
// 用法: node cdp_replace_book.mjs <旧书id前缀> <新epub绝对路径> <标签名>
const oldPrefix = process.argv[2];
const epubPath = process.argv[3];
const tagName = process.argv[4] || "VLM";
if (!oldPrefix || !epubPath) { console.log("用法: node cdp_replace_book.mjs <旧id前缀> <epub路径> [标签]"); process.exit(2); }

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
// 回收站旧条目（仅当其无进度——防线）
const old = await evalJS(`window.__bs.getBooks().then((bs) => bs.find((b) => b.id.startsWith(${JSON.stringify(oldPrefix)})))`);
if (old) {
  const prog = await evalJS(`window.__bs.getBooksWithStatus().then((bs) => { const x = bs.find((b) => b.id === ${JSON.stringify(old.id)}); return x && x.status ? (x.status.progressCurrent ?? x.status.progress_current ?? 0) : 0; })`);
  if (prog && Number(prog) > 0) { console.log(`旧条目有阅读进度(${prog})，拒绝替换`); process.exit(1); }
  await evalJS(`window.__bs.deleteBook(${JSON.stringify(old.id)})`);
  console.log(`旧条目 ${old.id.slice(0, 8)} 已入回收站`);
}
const tagId = await evalJS(`(async () => { const t = await window.__ts.getTagByName(${JSON.stringify(tagName)}); return t ? t.id : (await window.__ts.createTag({ name: ${JSON.stringify(tagName)}, color: "#3b82f6" })).id; })()`);
const before = await evalJS(`window.__bs.getBooks().then((bs) => bs.map((b) => b.id))`);
console.log("导入:", await evalJS(`window.__cs.importConvertedEpub(${JSON.stringify(staged)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`));
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
const after = await evalJS(`window.__bs.getBooks()`);
for (const b of after.filter((x) => !before.includes(x.id))) {
  await evalJS(`window.__bs.updateBook(${JSON.stringify(b.id)}, { tags: ${JSON.stringify([tagId])} })`);
  console.log(`${tagName} → ${(b.title || "").slice(0, 40)} [${b.id.slice(0, 8)}]`);
  await evalJS(`(async () => { const bv = await import("/src/services/task-executors/book-vectorize.ts"); bv.enqueueBookVectorize({ id: ${JSON.stringify(b.id)}, title: ${JSON.stringify(b.title || "")}, solo: true }); })()`);
  console.log("向量化入队 ✓");
}
process.exit(0);

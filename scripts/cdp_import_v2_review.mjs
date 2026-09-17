// v2 评审产物导入 dev 阅读器：8 本 VLM EPUB 导入+打标签，规则版民法总论补标，
// 零进度零笔记的测试重复条目移回收站（带阅读进度的旧版一律保留）。
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

// 1. 标签就绪（VLM / 规则）
const tagIds = await evalJS(`(async () => {
  const get = async (name, color) => {
    const t = await window.__ts.getTagByName(name);
    if (t) return t.id;
    return (await window.__ts.createTag({ name, color })).id;
  };
  return { vlm: await get("VLM", "#3b82f6"), rule: await get("规则", "#22c55e") };
})()`);
console.log("标签:", JSON.stringify(tagIds));

// 2. 导入 8 本 VLM EPUB
const base = "C:\\Users\\20995\\AppData\\Roaming\\com.bettersageread.dev\\converter\\_v2-review\\";
const files = [
  "高等数学 第七版 上册.epub",
  "A Modern Introduction to Quantum Field Theory.epub",
  "Feeling Great_ The Revolutionary New Treatment for Depression and Anxiety.epub",
  "汉语语义学.epub",
  "民法总论.epub",
  "_Society Must Be Defended__ Lectures at the Collège de France, 1975-76.epub",
  "伊豆の踊子.epub",
  "Born a Crime_ Stories from a South African Childhood.epub",
];
const before = await evalJS(`window.__bs.getBooks().then((bs) => bs.map((b) => b.id))`);
const imported = [];
for (const f of files) {
  const r = await evalJS(`window.__cs.importConvertedEpub(${JSON.stringify(base + f)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`);
  console.log(`导入 ${f}: ${r}`);
}
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
const after = await evalJS(`window.__bs.getBooks()`);
const newBooks = after.filter((b) => !before.includes(b.id));
console.log("新入库:", newBooks.length);

// 3. 新书打 VLM 标签
for (const b of newBooks) {
  await evalJS(`window.__bs.updateBook(${JSON.stringify(b.id)}, { tags: ${JSON.stringify([tagIds.vlm])} })`);
  console.log(`  VLM → ${(b.title || "").slice(0, 36)} [${b.id.slice(0, 8)}]`);
}

// 4. 规则版民法总论补标（保留既有标签）
await evalJS(`(async () => {
  const bs = await window.__bs.getBooks();
  const b = bs.find((x) => x.id === "202db74ab2ed58b0847da90b6f87ffa0");
  if (!b) return "missing";
  const tags = Array.from(new Set([...(b.tags || []), ${JSON.stringify(tagIds.rule)}]));
  await window.__bs.updateBook(b.id, { tags });
  return "ok";
})()`).then((r) => console.log("规则 → 民法总论(202db74a):", r));

// 5. 零进度零笔记测试重复条目 → 回收站（带阅读进度旧版一律保留）
const trash = [
  "0df82a55d186f69538bafe0004aa46e2", // Feeling Great (EN, 09-11 测试, 0 进度)
  "cffa57807b279ab2aabbdbeec528ec19", // 汉语语义学 (09-12 测试, 0 进度)
  "34dbc41fd325fa39340f562fe00fda9c", // Born a Crime (09-12 测试, 0 进度)
  "37c86688f2e7e455ebc6ece8c8a72f53", // Born a Crime (09-15 测试, 0 进度)
];
for (const id of trash) {
  const r = await evalJS(`window.__bs.deleteBook(${JSON.stringify(id)}).then(() => "ok").catch((e) => "ERR " + (e && e.message ? e.message : String(e)))`);
  console.log(`回收站 ${id.slice(0, 8)}: ${r}`);
}
await evalJS(`window.__lib.useLibraryStore.getState().refreshBooks()`);
console.log("最终库容:", (await evalJS(`window.__bs.getBooks()`)).length);
process.exit(0);

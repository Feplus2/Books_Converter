// 阅读器内截图：打开指定书 → 跳到指定进度 → 整页截图。
// 用法: node scripts/vlm_lab/shot_book.mjs <bookId> <fraction 0-1> <out.png> [wait秒]
const [bookId, fracArg, out] = process.argv.slice(2);
const frac = Number(fracArg || "0");
const waitS = Number(process.argv[5] || "8");
if (!bookId || !out) { console.error("用法: shot_book.mjs <bookId> <fraction> <out.png>"); process.exit(1); }

const list = await (await fetch("http://127.0.0.1:9223/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1420"));
if (!page) { console.error("dev 实例未就绪"); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let mid = 0; const pending = new Map();
const call = (m, p) => new Promise((res, rej) => { pending.set(++mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); });
ws.onmessage = (e) => { const msg = JSON.parse(e.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id).res(msg.result ?? msg); pending.delete(msg.id); } };
await new Promise((r) => (ws.onopen = r));
const evalJS = async (expr) => {
  const r = await call("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result?.value;
};

// 开书
await evalJS(`(async () => { window.__layout = await import("/src/store/layout-store.ts");
  window.__layout.useLayoutStore.getState().openBook(${JSON.stringify(bookId)}, "shot");
  window.__bs = await import("/src/services/book-service.ts"); })()`);
await new Promise((r) => setTimeout(r, 2000));
// 等加载完成 + foliate-view 出现
let ready = false;
for (let i = 0; i < 30 && !ready; i++) {
  await new Promise((r) => setTimeout(r, 1000));
  ready = await evalJS(`(() => !!document.querySelector("foliate-view"))()`);
}
if (!ready) { console.error("阅读器加载超时"); process.exit(1); }
// 先等加载/初始定位全部收尾，再跳进度，跳后短等再截屏
await new Promise((r) => setTimeout(r, waitS * 1000));
if (frac > 0) {
  const nav = await evalJS(`(() => { const v = document.querySelector("foliate-view");
    try { if (typeof v.goToFraction === "function") { v.goToFraction(${frac}); return "goToFraction ok"; }
      if (v.view && typeof v.view.goToFraction === "function") { v.view.goToFraction(${frac}); return "view.goToFraction ok"; }
      return "no goToFraction"; } catch (e) { return "ERR " + e.message; } })()`);
  console.log("导航:", nav);
  await new Promise((r) => setTimeout(r, 3000));
}
const shot = await call("Page.captureScreenshot", { format: "png" });
const { writeFileSync } = await import("node:fs");
writeFileSync(out, Buffer.from(shot.data, "base64"));
console.log("saved:", out);
process.exit(0);

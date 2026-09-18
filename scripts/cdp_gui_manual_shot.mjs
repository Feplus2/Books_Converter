// 新 GUI 手册页截图（验证"两种模式怎么选"新文案渲染）
const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
const ws = new WebSocket(page.webSocketDebuggerUrl);
let mid = 0; const pending = new Map();
const call = (m, p) => new Promise((res, rej) => { pending.set(++mid, res); ws.send(JSON.stringify({ id: mid, method: m, params: p })); });
ws.onmessage = (e) => { const msg = JSON.parse(e.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); } };
await new Promise((r) => (ws.onopen = r));
const evalJS = async (expr) => {
  const r = await call("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result.value;
};
// 点手册导航
await evalJS(`(() => { const els = [...document.querySelectorAll('nav *')]; const el = els.find((e) => e.textContent.trim() === '手册' && e.children.length === 0); if (el) el.click(); return !!el; })()`);
await new Promise((r) => setTimeout(r, 1200));
// 滚到"两种模式怎么选"
await evalJS(`(() => { const els = [...document.querySelectorAll('*')]; const el = els.find((e) => e.textContent.trim() === '两种模式怎么选' && e.children.length === 0); if (el) el.scrollIntoView({ block: 'start' }); return !!el; })()`);
await new Promise((r) => setTimeout(r, 600));
const shot = await call("Page.captureScreenshot", { format: "png" });
const fs = await import("node:fs");
fs.writeFileSync("F:/MyProjects/Books_Converter/_regress/vlm-lab/out/gui-manual-modes.png", Buffer.from(shot.data, "base64"));
console.log("截图已存");
process.exit(0);

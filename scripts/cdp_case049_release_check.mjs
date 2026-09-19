// 病例 049 E2E-A：release 新构建（CDP 9225）——验证产物架构说明随构建送达 + 队列页渲染截图
const list = await (await fetch("http://127.0.0.1:9225/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("tauri.localhost"));
if (!page) { console.log("找不到 tauri 页面"); process.exit(1); }
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
await call("Page.enable", {});
await new Promise((r) => setTimeout(r, 1500));
const dom = await evalJS(`(() => {
  const txt = document.body.innerText;
  return {
    有产物说明标题: txt.includes('产物目录长这样'),
    有自动清理句: txt.includes('中间产物自动清理'),
    有树形内容: txt.includes('vlm/ 或 mineru/'),
    有开始转换: txt.includes('开始转换'),
    有拖放区: txt.includes('拖入 PDF 加入队列'),
  };
})()`);
console.log('DOM 断言:', JSON.stringify(dom));
// 展开 details 让树形文案可见，再截图
await evalJS(`(() => { const d = [...document.querySelectorAll('details')].find((x) => x.textContent.includes('产物目录长这样')); if (d) d.open = true; return !!d; })()`);
await new Promise((r) => setTimeout(r, 400));
const shot = await call("Page.captureScreenshot", { format: "png" });
const fs = await import("node:fs");
fs.writeFileSync("F:/MyProjects/Books_Converter/_regress/case049-release-convert.png", Buffer.from(shot.data, "base64"));
console.log("截图已存 _regress/case049-release-convert.png");
process.exit(0);

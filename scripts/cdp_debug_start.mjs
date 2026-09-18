// 复现"开始转换没反应"：挂错误监听 → 点击 → 采样队列状态/toast/异常
const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
const ws = new WebSocket(page.webSocketDebuggerUrl);
let mid = 0; const pending = new Map();
const call = (m, p) => new Promise((res) => { pending.set(++mid, res); ws.send(JSON.stringify({ id: mid, method: m, params: p })); });
const console_ = [];
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
  if (msg.method === "Runtime.exceptionThrown") console_.push("EXC: " + JSON.stringify(msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text).slice(0, 400));
  if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type)) console_.push(msg.params.type + ": " + msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300));
};
await new Promise((r) => (ws.onopen = r));
const evalJS = async (expr) => {
  const r = await call("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await call("Runtime.enable", {});
// 现状
const before = await evalJS(`(() => {
  const txt = document.body.innerText;
  return { 转换页: txt.includes('开始转换'), 队列片段: txt.slice(txt.indexOf('转换队列'), txt.indexOf('转换队列') + 200) };
})()`);
console.log('现状:', JSON.stringify(before));
// 点击开始转换
const clicked = await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('开始转换') && !e.disabled); if (el) { el.click(); return true; } return 'disabled/notfound'; })()`);
console.log('点击:', clicked);
await sleep(5000);
const after = await evalJS(`(() => {
  const txt = document.body.innerText;
  const toasts = [...document.querySelectorAll('[class*=toast], [role=status], [role=alert]')].map((e) => e.textContent.trim()).filter(Boolean).slice(0, 4);
  return { toasts, 片段: txt.slice(txt.indexOf('转换队列'), txt.indexOf('转换队列') + 260) };
})()`);
console.log('5s 后:', JSON.stringify(after));
console.log('控制台异常:', JSON.stringify(console_.slice(0, 6)));
process.exit(0);

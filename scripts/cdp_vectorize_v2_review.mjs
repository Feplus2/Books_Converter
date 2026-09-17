// 触发 8 本新导入 VLM 书的向量化（task-center book-vectorize 通道，app 内排队执行）
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
const books = [
  ["dd8417057f6e235538547517e355a7bd", "Born a Crime"],
  ["806579f7a1c61ed4f2b1e147d8268773", "伊豆の踊子"],
  ["8c3ad7767e3586045a3f8609eeea9555", "Society Must Be Defended"],
  ["8c986e7c489cbf48ea2e8e37e70885ae", "民法总论"],
  ["cba64d416820a3ced68a8bd472639afe", "汉语语义学"],
  ["22f3aeacc1add1f89b04169dadfb651b", "Feeling Great"],
  ["c6207a35b9b8e0b5396d0b2ffc4f42b9", "QFT"],
  ["af66e389d3a8e32eeb8e9ba87adc51d2", "高等数学 第七版 上册"],
];
await evalJS(`(async () => { window.__bv = await import("/src/services/task-executors/book-vectorize.ts"); })()`);
for (const [id, title] of books) {
  const r = await evalJS(`window.__bv.enqueueBookVectorize({ id: ${JSON.stringify(id)}, title: ${JSON.stringify(title)}, solo: true }).ok`);
  console.log(`入队 ${title}: ${r}`);
}
process.exit(0);

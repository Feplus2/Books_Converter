// 验证 /models 拉取批量修复：进 DeepSeek 详情页 → 点拉取 → 数渲染出的模型行数
const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 设置 → 大模型提供商 → DeepSeek
await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').trim() === '设置'); if (el) el.click(); return !!el; })()`);
await sleep(700);
await evalJS(`(() => { const el = [...document.querySelectorAll('*')].find((e) => (e.textContent || '').trim() === '大模型提供商' && e.children.length === 0); if (el) el.click(); return !!el; })()`);
await sleep(700);
await evalJS(`(() => { const el = [...document.querySelectorAll('button, div.card, [role=button]')].find((e) => (e.textContent || '').includes('DeepSeek')); if (el) el.click(); return !!el; })()`);
await sleep(800);
console.log('详情页:', await evalJS(`document.body.innerText.includes('模型列表')`));
// 点拉取
const clicked = await evalJS(`(() => { const el = [...document.querySelectorAll('button')].find((e) => (e.textContent || '').includes('/models')); if (el) { el.click(); return true; } return false; })()`);
console.log('拉取按钮点击:', clicked);
await sleep(4000);
const info = await evalJS(`(() => {
  const rows = [...document.querySelectorAll('div.card')].filter((c) => c.querySelector('button, input[type=checkbox]') && (c.textContent || '').match(/deepseek|gpt|glm|v4/i));
  const ids = [...document.querySelectorAll('.mono')].map((e) => e.textContent.trim()).filter((t) => /^[a-z0-9][a-z0-9._-]+$/i.test(t) && t.length > 3);
  const toasts = [...document.querySelectorAll('*')].filter((e) => e.children.length === 0 && (e.textContent || '').includes('已拉取')).map((e) => e.textContent.trim());
  return { ids: [...new Set(ids)], toasts };
})()`);
console.log('渲染出的型号 id:', JSON.stringify(info.ids));
console.log('toast:', JSON.stringify(info.toasts));
process.exit(0);

// 手动驱动 queueStore 逐步执行，定位 startAll 卡点
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
const out = await evalJS(`(async () => {
  const qmod = await import("/src/lib/queue.ts");
  const smod = await import("/src/lib/settings.ts");
  const pmod = await import("/src/lib/precheck.ts");
  const q = qmod.queueStore ?? qmod.default;
  const s = smod.settingsStore ?? smod.default;
  const log = [];
  log.push('queueStore keys: ' + Object.keys(q).filter((k) => typeof q[k] === 'function').join(','));
  const tasks = q.tasks.map((t) => ({ id: t.id.slice(0, 8), status: t.status, mode: t.options.mode, out: t.options.outputDir }));
  log.push('tasks: ' + JSON.stringify(tasks));
  const t = q.tasks.find((x) => x.status === 'queued');
  if (!t) return log.join(' | ') + ' | 无 queued 任务';
  const settings = s.settings;
  log.push('settings.providers: ' + (settings.providers || []).length + ' ocr.mineruToken len: ' + (settings.ocr?.mineruToken || '').length + ' vlmModel: ' + t.options.vlmModel);
  try {
    const chk = pmod.precheckTask(t.options, settings);
    log.push('precheck: ' + JSON.stringify(chk));
  } catch (e) { log.push('precheck THREW: ' + e.message); }
  // 手动点 startAll 并计时
  const t0 = Date.now();
  try {
    const p = q.startAll();
    const race = await Promise.race([p.then(() => 'resolved'), new Promise((r) => setTimeout(() => r('TIMEOUT-8s'), 8000))]);
    log.push('startAll: ' + race + ' (' + (Date.now() - t0) + 'ms)');
  } catch (e) { log.push('startAll THREW: ' + e.message + ' ' + (e.stack || '').slice(0, 200)); }
  log.push('after: ' + JSON.stringify(q.tasks.map((x) => ({ id: x.id.slice(0, 8), status: x.status, err: x.error }))));
  log.push('started=' + q.started);
  return log.join('\\n');
})()`);
console.log(out);
process.exit(0);

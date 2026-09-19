// 病例 058 CDP：设置页「检查更新」区块实测 + 截图
import { writeFileSync } from "node:fs";

const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("localhost:1520"));
if (!page) { console.log("找不到 dev 页面"); process.exit(1); }
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
const shot = async (name) => {
  const s = await call("Page.captureScreenshot", { format: "png" });
  writeFileSync(`F:/MyProjects/Books_Converter/_regress/${name}`, Buffer.from(s.data, "base64"));
  console.log("截图:", name);
};
await call("Page.enable", {});

// 设置页 → 滚到「关于」组（检查更新按钮所在）
await evalJS(`(async () => { (await import("/src/lib/nav")).navigate({ page: "settings", section: "options" }); return true; })()`);
await sleep(1200);
await evalJS(`(() => {
  const el = [...document.querySelectorAll("*")].find((e) => e.textContent?.trim() === "Books Converter" && e.children.length === 0);
  el?.scrollIntoView({ block: "center" });
  return !!el;
})()`);
await sleep(500);
await shot("case058-settings-before.png");

// 点「检查更新」（真打 GitHub API，30s 超时上限）
await evalJS(`(() => {
  [...document.querySelectorAll("button")].find((b) => /检查更新/.test(b.textContent))?.click();
  return true;
})()`);
let msg = null;
for (let i = 0; i < 40; i++) {
  await sleep(1000);
  msg = await evalJS(`(() => {
    const t = document.body.innerText;
    const m = t.match(/当前已是最新版本|发现新版本：v[\\d.]+|检查更新失败[^\\n]*/);
    return m ? m[0] : null;
  })()`);
  if (msg) break;
}
console.log("检查结果:", msg ?? "(超时无消息)");
const hasLink = await evalJS(`document.body.innerText.includes("前往 Releases 下载")`);
console.log("「前往 Releases 下载」按钮在场:", hasLink);
await shot("case058-settings-update.png");
process.exit(0);

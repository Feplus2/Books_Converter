// 病例 059 CDP：设置页提示音行单开关形态确认 + 截图
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
const fails = [];
const check = (name, cond, extra = "") => { console.log(`${cond ? "✓" : "✗"} ${name}${extra ? " — " + extra : ""}`); if (!cond) fails.push(name); };

await evalJS(`(async () => { (await import("/src/lib/nav.ts")).navigate({ page: "settings", section: "options" }); return true; })()`);
await sleep(1200);
await evalJS(`(() => {
  const el = [...document.querySelectorAll("*")].find((e) => e.textContent?.trim() === "提示音" && e.children.length === 0);
  el?.scrollIntoView({ block: "center" });
  return !!el;
})()`);
await sleep(500);

// 行形态断言：提示音组内只有 Toggle，无试听按钮、无「完成提示音」旧文案
const row = await evalJS(`(() => {
  const all = [...document.querySelectorAll("*")];
  const grp = all.find((e) => e.textContent?.trim() === "提示音" && e.children.length === 0)?.closest(".card, section, div");
  // 找提示音组容器：含「转换提示音」label 的行
  const label = [...document.querySelectorAll("*")].find((e) => e.textContent?.trim() === "转换提示音" && e.children.length === 0);
  const row = label?.closest("div");
  const scope = label?.closest(".card") ?? document.body;
  return {
    hasLabel: !!label,
    desc: scope.textContent.includes("完成与失败都响"),
    noPreview: !document.body.innerText.includes("试听"),
    btnCount: row ? row.querySelectorAll("button.icon-btn").length : -1,
    hasToggle: !!row?.querySelector('[role="switch"], input[type="checkbox"], canvas'),
  };
})()`);
console.log(JSON.stringify(row));
check("行label=转换提示音", row.hasLabel);
check("说明文案=完成与失败都响", row.desc);
check("无「试听」残留", row.noPreview);
check("行内无 icon-btn（试听按钮已移除）", row.btnCount === 0);
await shot("case059-sound-row.png");

// 拨动开关验证写入（开→关→开还原）
const toggled = await evalJS(`(async () => {
  const m = await import("/src/lib/settings.ts");
  const before = m.settingsStore.settings.sound;
  m.settingsStore.update({ sound: !before });
  await new Promise((r) => setTimeout(r, 300));
  const mid = m.settingsStore.settings.sound;
  m.settingsStore.update({ sound: before });
  await new Promise((r) => setTimeout(r, 300));
  return { before, mid, after: m.settingsStore.settings.sound };
})()`);
check("开关写入生效并已还原", toggled.before === toggled.after && toggled.mid !== toggled.before, JSON.stringify(toggled));
await shot("case059-sound-row2.png");

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

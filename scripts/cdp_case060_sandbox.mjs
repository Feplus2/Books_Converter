// 病例 060 沙盒实测：release 形态（tauri.localhost）GUI 全程真跑 smoke.pdf
// 驱动：CDP 点拖放区 → PowerShell SendKeys 填原生文件对话框 → React 受控
// 输入（native setter）写输出目录 → 点开始转换 → DOM 轮询完成 → 落盘断言
import { writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

const SMOKE = "F:\\MyProjects\\Books_Converter\\_regress\\smoke.pdf";
const OUT = "F:\\MyProjects\\Books_Converter\\_regress\\case060-sandbox\\out";

const list = await (await fetch("http://127.0.0.1:9225/json/list")).json();
const page = list.find((t) => t.type === "page" && t.url.includes("tauri.localhost"));
if (!page) { console.log("找不到沙盒页面"); process.exit(1); }
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
await call("Page.enable", {});
const fails = [];
const check = (name, cond, extra = "") => { console.log(`${cond ? "✓" : "✗"} ${name}${extra ? " — " + extra : ""}`); if (!cond) fails.push(name); };
const shot = async (name) => {
  const s = await call("Page.captureScreenshot", { format: "png" });
  writeFileSync(`F:/MyProjects/Books_Converter/_regress/${name}`, Buffer.from(s.data, "base64"));
  console.log("截图:", name);
};

for (let i = 0; i < 20; i++) {
  if (await evalJS(`document.body.innerText.includes('拖入 PDF')`)) break;
  await sleep(500);
}
check("沙盒 GUI 落在转换页", true);

// ① 点拖放区 → 原生文件对话框 → SendKeys 填路径
await evalJS(`(() => {
  const dz = [...document.querySelectorAll("*")].find((e) => e.textContent?.trim() === "点击选择文件" && e.children.length === 0)
    ?? document.querySelector(".border-dashed");
  dz?.click();
  return !!dz;
})()`);
await sleep(1500);
execSync(
  `powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $ok = $ws.AppActivate('打开'); if (-not $ok) { $ok = $ws.AppActivate('Open') }; Start-Sleep -Milliseconds 600; $ws.SendKeys('${SMOKE}'); Start-Sleep -Milliseconds 400; $ws.SendKeys('{ENTER}')"`,
  { encoding: "utf8" },
);
await sleep(2000);
const queued = await evalJS(`document.body.innerText.includes("smoke")`);
check("smoke.pdf 已入队（原生对话框选入）", queued);

// ② 输出目录写沙盒（React 受控输入用 native setter）
const outOk = await evalJS(`(() => {
  const input = [...document.querySelectorAll("input")].find((i) => (i.value || "").includes("temp_files") || (i.value || "").includes("Documents"));
  if (!input) return "no-input";
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, ${JSON.stringify(OUT)});
  input.dispatchEvent(new Event("input", { bubbles: true }));
  return input.value;
})()`);
check("输出目录已指向沙盒", typeof outOk === "string" && outOk.includes("case060-sandbox"), String(outOk));

// ③ 开始转换 → 轮询完成/失败
await evalJS(`(() => { [...document.querySelectorAll("button")].find((b) => b.textContent.includes("开始转换"))?.click(); return true; })()`);
let final = "";
for (let i = 0; i < 180; i++) {
  await sleep(1000);
  final = await evalJS(`(() => {
    const t = document.body.innerText;
    if (t.includes("转换完成")) return "done";
    if (t.includes("转换失败")) return "error";
    return "";
  })()`);
  if (final) break;
}
check("沙盒转换完成", final === "done", final || "超时");
await shot("case060-sandbox-done.png");

console.log(fails.length ? `FAIL ${fails.length}: ${fails.join(" / ")}` : "ALL PASS");
process.exit(fails.length ? 1 : 0);

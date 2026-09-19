/// <reference types="node" />
// 病例 051 静态守卫：hover/focus/active/disabled 等状态伪类规则只许动自身视觉，
// 绝不允许动版面或命中区域（050 的 hover translateY 自激抖动通则化）。
// 失败方向 = 报错（测试红），不是静默放行——新增状态样式触发红灯时，
// 把新属性加进允许清单前必须先确认它不动 layout/hit-area。
// 实现注：css 走 node:fs（vitest 默认把 .css import 吞成空串，?raw/?inline 均失效）；
// tsx 走 import.meta.glob（对 ts/tsx 有效），不依赖 fs 递归。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const CSS_PATH = fileURLToPath(new URL("../../styles.css", import.meta.url));
const tsxFiles = import.meta.glob("../../**/*.{ts,tsx}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

// ── styles.css：状态伪类块的属性白名单 ──

const STATE_PSEUDO = /:(hover|focus|focus-visible|focus-within|active|checked|disabled|enabled)\b/;

// 只允许「自身视觉」属性：不动几何、不动布局、不动命中区域
const ALLOWED_STATE_PROPS = new Set([
  "color",
  "background",
  "background-color",
  "background-image",
  "border-color",
  "border-style", // 宽度不变时改线型不动版面
  "box-shadow",
  "filter",
  "opacity",
  "outline",
  "outline-color",
  "outline-style",
  "outline-width", // outline 不占盒模型
  "cursor",
  "text-decoration",
  "text-decoration-color",
  "text-decoration-line",
  "fill",
  "stroke",
  "caret-color",
  "accent-color",
  "visibility", // 只切显隐，占位保留，不顶兄弟
]);

// 白名单之外的显式禁令（给出更可读的报错）
const BANNED_STATE_PROPS = new Set([
  "transform",
  "translate",
  "scale",
  "rotate",
  "width",
  "height",
  "min-width",
  "min-height",
  "max-width",
  "max-height",
  "padding",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "padding-block",
  "padding-inline",
  "margin",
  "margin-top",
  "margin-right",
  "margin-bottom",
  "margin-left",
  "margin-block",
  "margin-inline",
  "top",
  "left",
  "right",
  "bottom",
  "inset",
  "position",
  "display",
  "content",
  "font-size",
  "font-weight",
  "line-height",
  "letter-spacing",
  "gap",
  "row-gap",
  "column-gap",
  "flex",
  "flex-grow",
  "flex-shrink",
  "flex-basis",
  "border-width",
  "border",
  "border-top",
  "border-right",
  "border-bottom",
  "border-left",
]);

type CssBlock = { chain: string[]; name: string; body: string };

/** 极简花括号解析：记录每个块的选择器链（看穿 @layer/@media 嵌套）与声明体 */
function parseBlocks(src: string): CssBlock[] {
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: CssBlock[] = [];
  const stack: string[] = [];
  let buf = "";
  for (const ch of clean) {
    if (ch === "{") {
      stack.push(buf.trim());
      buf = "";
    } else if (ch === "}") {
      const name = stack.pop() ?? "";
      const body = buf.trim();
      if (body) out.push({ chain: [...stack], name, body });
      buf = "";
    } else {
      buf += ch;
    }
  }
  return out;
}

function declaredProps(body: string): string[] {
  return body
    .split(";")
    .map((d) => d.split(":")[0].trim().toLowerCase())
    .filter(Boolean);
}

// ── tsx：状态 variant 工具类黑名单（Tailwind 类粒度过细，白名单易误伤） ──

const STATE_VARIANT =
  /(?:^|\s)((?:(?:group-hover|hover|focus|focus-visible|focus-within|active|disabled|enabled):)+)([^\s"'`]+)/g;

/** 状态 variant 下禁止的工具类：位移/缩放(>1.03)/盒尺寸/间距/字号字重字距/display 切换/边框宽度 */
const BANNED_VARIANT_UTILS: [RegExp, string][] = [
  [/^-?translate-/, "位移"],
  [/^scale-(?!10[0-3]$)/, "缩放（hover 只允许 scale-100~103 微放大）"],
  [/^[wh]-/, "宽/高"],
  [/^(min|max)-[wh]-/, "min/max 宽/高"],
  [/^[pm][xytrblse]?-/, "padding/margin"],
  [/^(top|left|right|bottom|inset)(-|$)/, "定位偏移"],
  [/^text-(2?xs|sm|base|lg|xl|[2-9]xl)$/, "字号"],
  [/^(leading|tracking|font)-/, "行高/字距/字重（改文字宽度会顶兄弟）"],
  [/^(gap|space)-/, "间距"],
  [
    /^(hidden|block|inline|inline-block|inline-flex|flex|grid|contents)$/,
    "display 切换（=hover 条件渲染撑版面）",
  ],
  [/^(grow|shrink|basis)(-|$)/, "flex 尺寸"],
  [/^border(-[xytrblse])?-(0|2|4|8)$/, "边框宽度"],
  [/^(columns|aspect|size|line-clamp)-/, "其他布局属性"],
];

describe("病例 051｜状态样式零版面位移守卫", () => {
  const css = readFileSync(CSS_PATH, "utf8");
  const stateBlocks = parseBlocks(css ?? "").filter((b) =>
    STATE_PSEUDO.test([...b.chain, b.name].join(" ")),
  );

  it("解析 sanity：styles.css 至少 5 个状态伪类块（防解析器静默失效）", () => {
    expect(css).toBeTruthy();
    expect(css).toContain(".lift:hover");
    expect(stateBlocks.length).toBeGreaterThanOrEqual(5);
  });

  it("状态伪类规则不含禁属性（transform/宽高/内外距/定位/display…）", () => {
    const hits: string[] = [];
    for (const b of stateBlocks) {
      const sel = [...b.chain, b.name].join(" ");
      for (const prop of declaredProps(b.body)) {
        if (BANNED_STATE_PROPS.has(prop)) hits.push(`${sel} { ${prop}: … }`);
      }
    }
    expect(hits, hits.join("\n")).toEqual([]);
  });

  it("状态伪类规则只含白名单视觉属性（color/background/border-color/box-shadow/filter/opacity/outline…）", () => {
    const hits: string[] = [];
    for (const b of stateBlocks) {
      const sel = [...b.chain, b.name].join(" ");
      for (const prop of declaredProps(b.body)) {
        if (!ALLOWED_STATE_PROPS.has(prop)) hits.push(`${sel} { ${prop} }`);
      }
    }
    expect(hits, hits.join("\n")).toEqual([]);
  });

  it("tsx 状态 variant（hover:/group-hover:/focus:…）不驱动布局或命中位移工具类", () => {
    const found: string[] = [];
    const hits: string[] = [];
    for (const [file, text] of Object.entries(tsxFiles)) {
      if (file.includes("__tests__")) continue;
      for (const m of text.matchAll(STATE_VARIANT)) {
        const token = m[1] + m[2];
        found.push(token);
        for (const [re, why] of BANNED_VARIANT_UTILS) {
          if (re.test(m[2])) hits.push(`${file} :: ${token}（${why}）`);
        }
      }
    }
    // sanity：当前至少存在 group-hover:opacity-100（ProductCard），防正则静默失效
    expect(found.length).toBeGreaterThanOrEqual(1);
    expect(hits, hits.join("\n")).toEqual([]);
  });
});

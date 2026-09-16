// 统一空态：线条风内联 SVG（主色 1.5px 描边、透明底、明暗主题随 CSS 变量）+ 标题 + 一句引导
import { S } from "../lib/strings";

export const EMPTY_KINDS = ["queue", "library", "notifications"] as const;
export type EmptyKind = (typeof EMPTY_KINDS)[number];

function Illustration({ kind }: { kind: EmptyKind }) {
  const common = {
    width: 72,
    height: 72,
    viewBox: "0 0 72 72",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (kind === "queue") {
    // 一叠纸 + 虚线投递箭头
    return (
      <svg {...common}>
        <rect x="10" y="12" width="26" height="36" rx="2.5" opacity="0.35" />
        <rect x="15" y="17" width="26" height="36" rx="2.5" opacity="0.6" />
        <rect x="20" y="22" width="26" height="36" rx="2.5" />
        <path d="M26 32h14M26 38h14M26 44h9" opacity="0.6" />
        <path d="M64 30H50" strokeDasharray="3 3" />
        <path d="M56 24l-6 6 6 6" />
      </svg>
    );
  }
  if (kind === "library") {
    // 书架上一本轮廓书
    return (
      <svg {...common}>
        <path d="M8 58h56" opacity="0.6" />
        <rect x="22" y="16" width="15" height="42" rx="2" />
        <path d="M29.5 16v42" opacity="0.6" />
        <rect x="40" y="24" width="12" height="34" rx="2" opacity="0.6" />
        <path d="M40 31h12" opacity="0.4" />
      </svg>
    );
  }
  // 铃铛轮廓 + 斜杠
  return (
    <svg {...common}>
      <path d="M36 14a13 13 0 0 0-13 13c0 9-3.5 13.5-6.5 15.5h39C53 40.5 49.5 36 49.5 27a13 13 0 0 0-13-13z" />
      <path d="M31.5 49a4.5 4.5 0 0 0 9 0" />
      <path d="M18 18l36 36" opacity="0.7" />
    </svg>
  );
}

export function EmptyState({ kind }: { kind: EmptyKind }) {
  const t = S.empty[kind];
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-center">
      <div style={{ color: "var(--accent)", opacity: 0.8 }}>
        <Illustration kind={kind} />
      </div>
      <div className="text-[14px] font-medium">{t.title}</div>
      <div className="text-xs" style={{ color: "var(--ink2)" }}>
        {t.hint}
      </div>
    </div>
  );
}

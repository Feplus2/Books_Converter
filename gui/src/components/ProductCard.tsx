// 产物卡片：书名 / 引擎徽标 / 格式徽标 / 完成时间 / 耗时 / 丢失标灰
import { BookOpen, CircleAlert } from "lucide-react";
import type { RegistryItem } from "../lib/registry";
import { S, formatElapsed } from "../lib/strings";
import { Badge } from "./Badge";

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(ts: string): string {
  // "2026-09-16T16:23:42+0800" → "2026-09-16 16:23"
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : ts;
}

const ENGINE_LABEL: Record<string, string> = {
  mineru: "MinerU",
  paddleocr: "PaddleOCR",
  vlm: "VLM",
};

export function ProductCard({
  item,
  onOpen,
}: {
  item: RegistryItem;
  onOpen: () => void;
}) {
  return (
    <button
      className="card lift w-full cursor-pointer p-4 text-left"
      style={item.missing ? { opacity: 0.55 } : undefined}
      onClick={onOpen}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
          style={{
            background: "color-mix(in srgb, var(--accent) 10%, transparent)",
            color: "var(--accent)",
          }}
        >
          <BookOpen size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{item.title || "（无标题）"}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge tone="accent">{ENGINE_LABEL[item.engine] ?? item.engine}</Badge>
            {item.formats.map((f) => (
              <Badge key={f} tone="muted">
                {f.toUpperCase()}
              </Badge>
            ))}
            {item.translate && <Badge tone="ok">译→{item.translate}</Badge>}
            {item.missing && (
              <Badge tone="err">
                <CircleAlert size={11} /> {S.library.badgeMissing}
              </Badge>
            )}
          </div>
          <div className="mono mt-1.5 text-xs" style={{ color: "var(--ink2)" }}>
            {fmtTime(item.ts)} · {formatElapsed(item.elapsed_s)}
          </div>
        </div>
      </div>
    </button>
  );
}

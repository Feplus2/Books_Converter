// 轻量 Tooltip：fixed 定位，hover 150ms 淡入，不用原生 title
import { useRef, useState, type ReactNode } from "react";

export function Tooltip({
  label,
  children,
  side = "top",
}: {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom" | "right" | "left";
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const show = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (side === "top") setPos({ x: r.left + r.width / 2, y: r.top - 8 });
    else if (side === "bottom") setPos({ x: r.left + r.width / 2, y: r.bottom + 8 });
    else if (side === "right") setPos({ x: r.right + 8, y: r.top + r.height / 2 });
    else setPos({ x: r.left - 8, y: r.top + r.height / 2 });
  };

  return (
    <span
      ref={ref}
      className="inline-flex"
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
    >
      {children}
      {pos && (
        <span
          className="pointer-events-none fixed z-50 rounded-md px-2 py-1 text-xs whitespace-nowrap"
          style={{
            left: pos.x,
            top: pos.y,
            transform:
              side === "top"
                ? "translate(-50%, -100%)"
                : side === "bottom"
                  ? "translate(-50%, 0)"
                  : side === "right"
                    ? "translate(0, -50%)"
                    : "translate(-100%, -50%)",
            background: "var(--ink)",
            color: "var(--bg)",
            animation: "page-in 150ms ease-out",
          }}
        >
          {label}
        </span>
      )}
    </span>
  );
}

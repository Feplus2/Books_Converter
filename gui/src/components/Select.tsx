// 自绘下拉：按钮 + 浮层列表（hover 主色光晕、选中勾/高亮、disabled 灰显+原因徽标，
// ESC/外点/滚动/缩放关闭），完全替代原生 <select>
import { S } from "../lib/strings";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}

export function Select({
  value,
  options,
  onChange,
  placeholder = "",
  width = 224,
  disabled = false,
}: {
  value: string;
  options: SelectOption[];
  onChange: (v: string) => void;
  placeholder?: string;
  width?: number;
  disabled?: boolean;
}) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [rect, setRect] = useState<{ left: number; top: number; up: boolean } | null>(null);

  const selected = options.find((o) => o.value === value);

  const toggle = () => {
    if (disabled) return;
    if (open) {
      setOpen(false);
      return;
    }
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const panelH = Math.min(300, options.length * 36 + 12);
    const up = r.bottom + panelH + 8 > window.innerHeight && r.top - panelH - 8 > 0;
    setRect({ left: r.left, top: up ? r.top - panelH - 6 : r.bottom + 6, up });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        panelRef.current?.contains(e.target as Node) ||
        btnRef.current?.contains(e.target as Node)
      )
        return;
      setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onScroll = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="input flex items-center justify-between gap-2 text-left"
        style={{ width, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1 }}
        onClick={toggle}
      >
        <span className="min-w-0 flex-1 truncate" style={selected ? undefined : { color: "var(--ink2)" }}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown
          size={14}
          className="shrink-0 transition-transform duration-150"
          style={{ color: "var(--ink2)", transform: open ? "rotate(180deg)" : undefined }}
        />
      </button>
      {open && rect && (
        <div
          ref={panelRef}
          className="card fixed z-50 overflow-y-auto p-1"
          style={{
            left: rect.left,
            top: rect.top,
            width,
            maxHeight: 300,
            animation: "page-in 150ms ease-out",
            boxShadow: "0 8px 30px rgb(0 0 0 / 0.18)",
          }}
        >
          {options.map((o) => {
            const active = o.value === value;
            return (
              <button
                key={o.value}
                type="button"
                disabled={o.disabled}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-all duration-150"
                style={{
                  cursor: o.disabled ? "not-allowed" : "pointer",
                  opacity: o.disabled ? 0.5 : 1,
                  color: active ? "var(--accent)" : "var(--ink)",
                  background: "transparent",
                }}
                onMouseEnter={(e) => {
                  if (o.disabled) return;
                  e.currentTarget.style.background =
                    "color-mix(in srgb, var(--accent) 8%, transparent)";
                  e.currentTarget.style.boxShadow =
                    "0 0 12px color-mix(in srgb, var(--accent) 12%, transparent)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.boxShadow = "none";
                }}
                onClick={() => {
                  if (o.disabled) return;
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                <span className="w-4 shrink-0">
                  {active && <Check size={13} style={{ color: "var(--accent)" }} />}
                </span>
                <span className="min-w-0 flex-1 truncate">{o.label}</span>
                {o.disabled && o.disabledReason && (
                  <span
                    className="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                    style={{
                      background: "color-mix(in srgb, var(--warn) 14%, transparent)",
                      color: "var(--warn)",
                    }}
                  >
                    {o.disabledReason}
                  </span>
                )}
              </button>
            );
          })}
          {!options.length && (
            <div className="px-3 py-4 text-center text-xs" style={{ color: "var(--ink2)" }}>
              {placeholder || S.select.emptyOptions}
            </div>
          )}
        </div>
      )}
    </>
  );
}

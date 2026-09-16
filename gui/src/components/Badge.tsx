// 徽标
const TONES: Record<string, { bg: string; fg: string }> = {
  accent: { bg: "color-mix(in srgb, var(--accent) 12%, transparent)", fg: "var(--accent)" },
  ok: { bg: "color-mix(in srgb, var(--ok) 12%, transparent)", fg: "var(--ok)" },
  warn: { bg: "color-mix(in srgb, var(--warn) 14%, transparent)", fg: "var(--warn)" },
  err: { bg: "color-mix(in srgb, var(--err) 12%, transparent)", fg: "var(--err)" },
  muted: { bg: "color-mix(in srgb, var(--ink) 8%, transparent)", fg: "var(--ink2)" },
};

export function Badge({
  tone = "muted",
  children,
}: {
  tone?: keyof typeof TONES;
  children: React.ReactNode;
}) {
  const t = TONES[tone];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
      style={{ background: t.bg, color: t.fg }}
    >
      {children}
    </span>
  );
}

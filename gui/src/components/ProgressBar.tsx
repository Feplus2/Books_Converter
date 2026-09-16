// 进度条：细轨 + 主色填充 + 阶段步进点
export function ProgressBar({
  percent,
  stage,
  stagesTotal,
}: {
  percent: number;
  stage: number | null;
  stagesTotal: number;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="progress-track flex-1">
        <div className="progress-fill" style={{ width: `${Math.min(percent, 100)}%` }} />
        {/* 阶段步进点 */}
        {Array.from({ length: stagesTotal }, (_, i) => i + 1).map((s) => {
          const left = (s / stagesTotal) * 100;
          const reached = stage != null && s <= stage;
          return (
            <span
              key={s}
              className="absolute top-1/2 h-[8px] w-[8px] rounded-full transition-colors duration-150"
              style={{
                left: `${left}%`,
                transform: "translate(-50%, -50%)",
                background: reached ? "var(--accent)" : "var(--card)",
                border: `1.5px solid ${reached ? "var(--accent)" : "color-mix(in srgb, var(--ink) 25%, transparent)"}`,
              }}
            />
          );
        })}
      </div>
      <span className="mono w-12 text-right text-xs" style={{ color: "var(--ink2)" }}>
        {percent.toFixed(1)}%
      </span>
    </div>
  );
}

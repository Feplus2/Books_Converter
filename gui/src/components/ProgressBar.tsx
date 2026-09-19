// 进度条：细轨 + 主色填充 + 阶段步进点
// 步进点位置 = start 事件 stage_bounds（按预估耗时加权的真实阶段边界）；
// 无边界数据（旧 sidecar）时不画点——宁可不画，不错画（铁律 0）
export function ProgressBar({
  percent,
  stage,
  bounds,
}: {
  percent: number;
  stage: number | null;
  bounds: number[] | null;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="progress-track flex-1">
        <div className="progress-fill" style={{ width: `${Math.min(percent, 100)}%` }} />
        {/* 阶段步进点 */}
        {bounds?.map((left, i) => {
          const reached = stage != null && i + 1 <= stage;
          return (
            <span
              key={i}
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

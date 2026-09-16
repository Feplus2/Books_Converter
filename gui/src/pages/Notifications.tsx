// 通知：独立页（与设置/手册同级）。历史列表全页展示，可清空/逐条删除；打开即全部已读
import { useEffect } from "react";
import { BellOff, CircleAlert, CircleCheck, Info, Trash2, TriangleAlert, X } from "lucide-react";
import { notifyStore, useNotices, type NotifyLevel } from "../lib/notify";
import { S } from "../lib/strings";
import { Tooltip } from "../components/Tooltip";

const LEVEL_ICON: Record<NotifyLevel, { icon: typeof Info; color: string }> = {
  success: { icon: CircleCheck, color: "var(--ok)" },
  error: { icon: CircleAlert, color: "var(--err)" },
  warning: { icon: TriangleAlert, color: "var(--warn)" },
  info: { icon: Info, color: "var(--accent)" },
};

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function NotificationsPage() {
  const notices = useNotices();

  useEffect(() => {
    notifyStore.markAllRead();
  }, [notices.length]);

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[14px] font-semibold" style={{ color: "var(--ink2)" }}>
          {S.notify.title}
        </div>
        {notices.length > 0 && (
          <button className="btn btn-ghost text-xs" onClick={() => notifyStore.clear()}>
            <Trash2 size={13} /> {S.notify.clear}
          </button>
        )}
      </div>
      {notices.length === 0 ? (
        <div
          className="card flex flex-col items-center gap-2 py-16 text-xs"
          style={{ color: "var(--ink2)" }}
        >
          <BellOff size={20} />
          {S.notify.empty}
        </div>
      ) : (
        <div className="flex flex-col gap-2 pb-8">
          {notices.map((n) => {
            const L = LEVEL_ICON[n.level];
            return (
              <div key={n.id} className="card lift flex items-start gap-3 px-3 py-2.5">
                <L.icon size={15} className="mt-0.5 shrink-0" style={{ color: L.color }} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] leading-6 break-words">{n.text}</div>
                  <div className="mono text-[11px]" style={{ color: "var(--ink2)" }}>
                    {fmtTime(n.ts)}
                  </div>
                </div>
                <Tooltip label={S.notify.deleteOne}>
                  <button
                    className="icon-btn shrink-0"
                    style={{ width: 26, height: 26 }}
                    onClick={() => notifyStore.remove(n.id)}
                  >
                    <X size={13} />
                  </button>
                </Tooltip>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

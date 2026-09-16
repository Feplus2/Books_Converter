import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  BookCheck,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Loader2,
  Clock3,
  TriangleAlert,
  X,
} from "lucide-react";
import { queueStore, type QueueTask } from "../lib/queue";
import { notify } from "../lib/notify";
import { S, formatElapsed } from "../lib/strings";
import { Badge } from "./Badge";
import { ProgressBar } from "./ProgressBar";
import { Tooltip } from "./Tooltip";

function StatusBadge({ task }: { task: QueueTask }) {
  switch (task.status) {
    case "queued":
      return (
        <Badge tone="muted">
          <Clock3 size={11} /> {S.convert.statusQueued}
        </Badge>
      );
    case "running":
      return (
        <Badge tone="accent">
          <Loader2 size={11} className="animate-spin" /> {S.convert.statusRunning}
        </Badge>
      );
    case "done":
      return (
        <Badge tone="ok">
          <CircleCheck size={11} /> {S.convert.statusDone}
        </Badge>
      );
    case "error":
      return (
        <Badge tone="err">
          <CircleAlert size={11} /> {S.convert.statusError}
        </Badge>
      );
    case "cancelled":
      return <Badge tone="muted">{S.convert.statusCancelled}</Badge>;
  }
}

export function QueueItem({ task }: { task: QueueTask }) {
  const [expanded, setExpanded] = useState(false);
  const cancellable = task.status === "queued" || task.status === "running";

  return (
    <div className="card lift p-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{task.title}</span>
            <StatusBadge task={task} />
            {task.elapsed != null && (
              <span className="mono text-xs" style={{ color: "var(--ink2)" }}>
                {formatElapsed(task.elapsed)}
              </span>
            )}
          </div>
          {(task.status === "running" || task.status === "done") && (
            <div className="mt-2">
              <ProgressBar
                percent={task.percent}
                stage={task.stage}
                stagesTotal={task.stagesTotal}
              />
            </div>
          )}
          {task.status === "running" && task.stageName && (
            <div className="mt-1 text-xs" style={{ color: "var(--ink2)" }}>
              阶段 {task.stage}/{task.stagesTotal} · {task.stageName}
            </div>
          )}
          {task.stalled && task.status === "running" && (
            <div
              className="mt-1 flex items-center gap-1 text-xs"
              style={{ color: "var(--warn)" }}
            >
              <TriangleAlert size={12} /> {S.convert.stalled}
            </div>
          )}
          {task.error && (
            <div className="mt-1 truncate text-xs" style={{ color: "var(--err)" }} title={task.error}>
              {task.error}
            </div>
          )}
        </div>

        {task.status === "done" && task.epubPath && (
          <button
            className="btn btn-ghost shrink-0"
            onClick={() =>
              invoke("open_file", { path: task.epubPath }).catch((e) =>
                notify.error(String(e)),
              )
            }
          >
            <BookCheck size={14} /> {S.convert.openEpub}
          </button>
        )}
        <Tooltip label={expanded ? S.convert.collapseLog : S.convert.expandLog}>
          <button className="icon-btn" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </button>
        </Tooltip>
        {cancellable && (
          <Tooltip label={S.convert.cancelTooltip}>
            <button className="icon-btn" onClick={() => queueStore.cancel(task.id)}>
              <X size={16} />
            </button>
          </Tooltip>
        )}
      </div>

      {expanded && (
        <div
          className="mono mt-2 max-h-48 overflow-y-auto rounded-lg p-2 text-xs leading-5 whitespace-pre-wrap"
          style={{ background: "var(--card2)", color: "var(--ink2)" }}
        >
          {task.errorDetail && (
            <div className="mb-1" style={{ color: "var(--err)" }}>
              [{S.convert.techDetail}] {task.errorDetail}
            </div>
          )}
          {task.logs.length ? task.logs.join("\n") : "（暂无日志）"}
        </div>
      )}
    </div>
  );
}

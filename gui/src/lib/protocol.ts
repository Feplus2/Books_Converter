// headless 进度协议（progress_headless.py 逐字契约）
export type PipelineEvent =
  | {
      type: "start";
      title: string;
      engine: string; // 真实引擎 id（mineru/paddleocr/vlm）
      translate: boolean;
      /** 按预估耗时加权的阶段边界（累计百分比），进度条刻度点用；旧 sidecar 无此字段 */
      stage_bounds?: number[];
    }
  | {
      type: "progress";
      stage?: number;
      stage_name?: string;
      detail?: string;
      fraction?: number | null;
      percent: number;
    }
  | { type: "stage_done"; stage: number; stage_name: string; elapsed: number; percent: number }
  | { type: "done"; epub_path: string; title: string; elapsed: number; percent: number; product_dir?: string }
  | { type: "error"; message: string }
  | { type: "log"; line: string }; // Rust 侧包装 stderr 行

export function parsePipelineEvent(line: string): PipelineEvent | null {
  try {
    const obj = JSON.parse(line);
    if (obj && typeof obj.type === "string") return obj as PipelineEvent;
    return null;
  } catch {
    return null;
  }
}

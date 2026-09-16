// headless 进度协议（progress_headless.py 逐字契约）
export type PipelineEvent =
  | { type: "start"; title: string; engine: string; translate: boolean }
  | {
      type: "progress";
      stage?: number;
      stage_name?: string;
      detail?: string;
      fraction?: number | null;
      percent: number;
    }
  | { type: "stage_done"; stage: number; stage_name: string; elapsed: number; percent: number }
  | { type: "done"; epub_path: string; title: string; elapsed: number; percent: number }
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

export const STAGE_NAMES: Record<number, string> = {
  1: "解析引擎",
  2: "结构分析",
  3: "翻译",
  4: "EPUB/导出",
};

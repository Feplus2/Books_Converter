// 病例 050：进度事件契约（start 带真实 engine + stage_bounds）与引擎显示名
import { describe, expect, it } from "vitest";
import { parsePipelineEvent } from "../protocol";
import { engineName } from "../strings";

describe("进度事件契约（progress_headless.py 逐字对齐）", () => {
  it("start 事件携带真实 engine 与 stage_bounds", () => {
    const ev = parsePipelineEvent(
      JSON.stringify({
        type: "start",
        title: "书",
        engine: "vlm",
        translate: false,
        stage_bounds: [84.2, 98.9, 100.0],
      }),
    );
    expect(ev?.type).toBe("start");
    if (ev?.type === "start") {
      expect(ev.engine).toBe("vlm");
      expect(ev.stage_bounds).toEqual([84.2, 98.9, 100.0]);
    }
  });

  it("旧 sidecar 的 start 事件无 stage_bounds → undefined（前端不画点）", () => {
    const ev = parsePipelineEvent(
      JSON.stringify({ type: "start", title: "书", engine: "mineru", translate: false }),
    );
    expect(ev?.type).toBe("start");
    if (ev?.type === "start") expect(ev.stage_bounds).toBeUndefined();
  });

  it("progress 事件 detail 字段透传（卡片常显详情行用）", () => {
    const ev = parsePipelineEvent(
      JSON.stringify({
        type: "progress",
        stage: 1,
        stage_name: "VLM",
        detail: "VLM 阅读 120/549 页",
        fraction: 120 / 549,
        percent: 18.3,
      }),
    );
    expect(ev?.type).toBe("progress");
    if (ev?.type === "progress") expect(ev.detail).toBe("VLM 阅读 120/549 页");
  });
});

describe("engineName 引擎显示名", () => {
  it("三引擎映射", () => {
    expect(engineName("mineru")).toBe("MinerU");
    expect(engineName("paddleocr")).toBe("PaddleOCR");
    expect(engineName("vlm")).toBe("VLM");
  });

  it("未知 id 原样透传（不动作）", () => {
    expect(engineName("hybrid")).toBe("hybrid");
    expect(engineName("")).toBe("");
  });
});

// 缺 key 预检（纯函数，vitest 覆盖）：开始转换前逐项检查，失败方向=不启动该项
import {
  findProvider,
  splitModelRef,
  type ConvertOptions,
  type Settings,
} from "./settings";
import { S } from "./strings";

export type SettingsSection = "parse" | "providers" | "options";

export type PrecheckResult =
  | { ok: true }
  | { ok: false; missing: string; section: SettingsSection; providerId?: string };

export function precheckTask(o: ConvertOptions, s: Settings): PrecheckResult {
  if (o.mode === "rule") {
    if (o.ruleEngine === "mineru" && !s.ocr.mineruToken.trim()) {
      return { ok: false, missing: S.precheck.mineruToken, section: "parse" };
    }
    if (o.ruleEngine === "paddleocr" && !s.ocr.paddleocrToken.trim()) {
      return { ok: false, missing: S.precheck.paddleocrToken, section: "parse" };
    }
    // 后处理模型：选了才查（未选走 .env 兜底，前端无从校验）
    const post = splitModelRef(o.postModel);
    if (post) {
      const p = findProvider(s, post.providerId);
      if (!p || !p.apiKey.trim()) {
        return {
          ok: false,
          missing: S.precheck.postModelKey(post.modelId),
          section: "providers",
          providerId: post.providerId,
        };
      }
    }
    return { ok: true };
  }

  // VLM 模式：多模态模型必选，所属提供商 key 必填
  const vlm = splitModelRef(o.vlmModel);
  if (!vlm) {
    return { ok: false, missing: S.precheck.vlmNoModel, section: "providers" };
  }
  const p = findProvider(s, vlm.providerId);
  if (!p || !p.apiKey.trim()) {
    return {
      ok: false,
      missing: S.precheck.vlmModelKey(vlm.modelId),
      section: "providers",
      providerId: vlm.providerId,
    };
  }
  return { ok: true };
}

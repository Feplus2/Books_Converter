// 「再次转换」选项重组装（纯函数，vitest 覆盖）：
// 用 registry 记录重放 ConvertOptions；registry v1 没有的字段（md_split 等）回退当前设置默认。
import {
  activatedModels,
  type ConvertOptions,
  type Settings,
} from "./settings";

export interface ReconvertSource {
  engine: string;
  ocr: boolean;
  translate: string | null;
  formats: string[];
  vlm_model?: string | null; // 老记录无此字段 → undefined
  vlm_reasoning?: string | null;
  registryDir: string; // _registry.jsonl 所在目录 = 当时的输出目录
}

const VALID_FORMATS = new Set(["epub", "md", "tex"]);
const REASONING_LEVELS = new Set(["off", "low", "medium", "high"]);

export interface ReconvertResult {
  options: ConvertOptions;
  /** VLM 记录的原模型已不在激活列表（回退了默认，需 toast 提示） */
  modelFallback: boolean;
}

export function buildReconvertOptions(src: ReconvertSource, s: Settings): ReconvertResult {
  // 以当前设置默认为底（覆盖 md_split/md_dialect/tex_full/export_lang/workers 等未登记字段）
  const o: ConvertOptions = { ...s.defaults, formats: [...s.defaults.formats] };
  let modelFallback = false;

  o.outputDir = src.registryDir;
  o.ocr = src.ocr;
  o.translate = !!src.translate;
  if (src.translate) o.translateLang = src.translate;
  const formats = src.formats.filter((f) => VALID_FORMATS.has(f));
  o.formats = formats.length ? formats : ["epub"];

  if (src.engine === "vlm") {
    o.mode = "vlm";
    // 原模型若仍在已激活视觉列表则预选（按型号 id 匹配）；否则回退默认并提示
    const visionModels = activatedModels(s, true);
    const hit = src.vlm_model ? visionModels.find((m) => m.modelId === src.vlm_model) : undefined;
    if (hit) {
      o.vlmModel = hit.ref;
      if (src.vlm_reasoning && REASONING_LEVELS.has(src.vlm_reasoning)) {
        o.reasoning = src.vlm_reasoning as ConvertOptions["reasoning"];
      }
    } else if (src.vlm_model) {
      modelFallback = true;
    }
  } else {
    o.mode = "rule";
    o.ruleEngine = src.engine === "paddleocr" ? "paddleocr" : "mineru";
  }
  return { options: o, modelFallback };
}

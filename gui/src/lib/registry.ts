// 产物登记处类型（pipeline.py _register_product schema）
import type { Settings } from "./settings";

export interface RegistryEntry {
  v: number;
  ts: string;
  title: string;
  source_pdf: string;
  work_dir: string;
  engine: string;
  ocr: boolean;
  translate: string | null;
  formats: string[];
  products: Record<string, string[]>;
  elapsed_s: number;
  app_version: string;
}

export interface ProductFile {
  fmt: string;
  path: string;
  name: string;
  exists: boolean;
  is_dir: boolean;
  size: number;
}

export interface RegistryItem extends RegistryEntry {
  registry_path: string;
  files: ProductFile[];
  missing: boolean;
  source_exists: boolean;
  vlm_model?: string | null;
  vlm_reasoning?: string | null;
}

/** 产物库数据来源（病例 053 纯登记制）：只读登记文件所在目录——
 *  当前输出目录 + 历史输出目录，去重去空。不做目录扫描
 *  （扫描曾把通用目录里的本机文档误判进产物库，已移除）。 */
export function registryDirsOf(s: Settings): string[] {
  return [...new Set([s.defaults.outputDir, ...s.historyDirs].filter(Boolean))];
}

/** FileRow 按钮决策（病例 057 纯函数，vitest 断言）：
 *  open 按钮恒在（tooltip 目录=打开文件夹/文件=打开文件）；
 *  reveal（在文件夹显示）只对文件渲染——目录的 reveal 与 open 等价，
 *  且旧实现 explorer /select 目录必翻车回落「文档」主文件夹。 */
export function fileRowActions(file: Pick<ProductFile, "is_dir">): {
  openTooltip: "openFile" | "openFolder";
  showReveal: boolean;
} {
  return {
    openTooltip: file.is_dir ? "openFolder" : "openFile",
    showReveal: !file.is_dir,
  };
}

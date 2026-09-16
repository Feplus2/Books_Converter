// 产物登记处类型（pipeline.py _register_product schema）
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
}

export interface UnregisteredItem {
  path: string;
  name: string;
  fmt: string;
  size: number;
}

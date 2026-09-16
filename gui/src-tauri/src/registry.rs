//! 产物登记处读取/回写：`<输出目录>/_registry.jsonl` 每行一条 JSON。
//! schema 与 pipeline.py `_register_product` 逐字对齐。失败方向=不动作：
//! 单行解析失败跳过该行，绝不丢其他行。

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegistryEntry {
    #[serde(default)]
    pub v: u32,
    pub ts: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub source_pdf: String,
    #[serde(default)]
    pub work_dir: String,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub ocr: bool,
    #[serde(default)]
    pub translate: Option<String>,
    #[serde(default)]
    pub formats: Vec<String>,
    #[serde(default)]
    pub products: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub elapsed_s: f64,
    #[serde(default)]
    pub app_version: String,
}

#[derive(Debug, Serialize)]
pub struct ProductFile {
    pub fmt: String,
    pub path: String,
    pub name: String,
    pub exists: bool,
    pub is_dir: bool,
    pub size: u64,
}

#[derive(Debug, Serialize)]
pub struct RegistryItem {
    pub registry_path: String,
    #[serde(flatten)]
    pub entry: RegistryEntry,
    pub files: Vec<ProductFile>,
    pub missing: bool,
}

#[derive(Debug, Serialize)]
pub struct UnregisteredItem {
    pub path: String,
    pub name: String,
    pub fmt: String,
    pub size: u64,
}

fn path_size(p: &Path) -> u64 {
    if p.is_file() {
        return p.metadata().map(|m| m.len()).unwrap_or(0);
    }
    if p.is_dir() {
        let mut total = 0u64;
        let mut stack = vec![p.to_path_buf()];
        while let Some(d) = stack.pop() {
            if let Ok(rd) = fs::read_dir(&d) {
                for e in rd.flatten() {
                    let ep = e.path();
                    if ep.is_dir() {
                        stack.push(ep);
                    } else {
                        total += e.metadata().map(|m| m.len()).unwrap_or(0);
                    }
                }
            }
        }
        return total;
    }
    0
}

fn product_file(fmt: &str, path: &str) -> ProductFile {
    let p = Path::new(path);
    let exists = p.exists();
    let name = p
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string());
    ProductFile {
        fmt: fmt.to_string(),
        path: path.to_string(),
        name,
        exists,
        is_dir: p.is_dir(),
        size: if exists { path_size(p) } else { 0 },
    }
}

/// 汇总读多个输出目录的 _registry.jsonl，并校验每个产物文件的存在性。
#[tauri::command]
pub fn read_registry(dirs: Vec<String>) -> Vec<RegistryItem> {
    let mut items = Vec::new();
    let mut seen_files = HashSet::new();
    for dir in dirs {
        let reg = Path::new(&dir).join("_registry.jsonl");
        let Ok(content) = fs::read_to_string(&reg) else {
            continue;
        };
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(entry) = serde_json::from_str::<RegistryEntry>(line) else {
                continue; // 坏行跳过，不动作
            };
            let mut files = Vec::new();
            for fmt in &entry.formats {
                if let Some(paths) = entry.products.get(fmt) {
                    for p in paths {
                        files.push(product_file(fmt, p));
                        seen_files.insert(p.clone());
                    }
                }
            }
            let missing = files.iter().any(|f| !f.exists);
            items.push(RegistryItem {
                registry_path: reg.to_string_lossy().into_owned(),
                entry,
                files,
                missing,
            });
        }
    }
    items.sort_by(|a, b| b.entry.ts.cmp(&a.entry.ts));
    items
}

/// 扫描目录里的 epub/md/tex 文件，把未登记进任何 registry 的列为「未登记」。
/// 深度限 4 层，防误扫巨型目录树。
#[tauri::command]
pub fn scan_unregistered(dirs: Vec<String>) -> Vec<UnregisteredItem> {
    let mut registered: HashSet<PathBuf> = HashSet::new();
    for dir in &dirs {
        let reg = Path::new(dir).join("_registry.jsonl");
        if let Ok(content) = fs::read_to_string(&reg) {
            for line in content.lines() {
                if let Ok(entry) = serde_json::from_str::<RegistryEntry>(line.trim()) {
                    for paths in entry.products.values() {
                        for p in paths {
                            registered.insert(PathBuf::from(p));
                        }
                    }
                }
            }
        }
    }

    let mut out = Vec::new();
    for dir in &dirs {
        let root = PathBuf::from(dir);
        if !root.is_dir() {
            continue;
        }
        let mut stack: Vec<(PathBuf, u32)> = vec![(root, 0)];
        while let Some((d, depth)) = stack.pop() {
            if depth > 4 {
                continue;
            }
            let Ok(rd) = fs::read_dir(&d) else {
                continue;
            };
            for e in rd.flatten() {
                let p = e.path();
                // 已登记路径（含 md 分章目录）整棵跳过
                if registered.iter().any(|r| p.starts_with(r)) {
                    continue;
                }
                if p.is_dir() {
                    stack.push((p, depth + 1));
                    continue;
                }
                let ext = p
                    .extension()
                    .map(|s| s.to_string_lossy().to_lowercase())
                    .unwrap_or_default();
                if matches!(ext.as_str(), "epub" | "md" | "tex") {
                    out.push(UnregisteredItem {
                        name: p
                            .file_stem()
                            .map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_default(),
                        fmt: ext,
                        size: e.metadata().map(|m| m.len()).unwrap_or(0),
                        path: p.to_string_lossy().into_owned(),
                    });
                }
            }
        }
    }
    out.sort_by(|a, b| b.path.cmp(&a.path));
    out
}

fn rewrite_jsonl(reg: &Path, lines: &[String]) -> Result<(), String> {
    let tmp = reg.with_extension("jsonl.tmp");
    fs::write(&tmp, lines.join("\n") + "\n").map_err(|e| format!("写入临时文件失败: {e}"))?;
    if reg.exists() {
        fs::remove_file(reg).map_err(|e| format!("替换登记文件失败: {e}"))?;
    }
    fs::rename(&tmp, reg).map_err(|e| format!("重命名登记文件失败: {e}"))?;
    Ok(())
}

/// 「重新定位」：把 ts 行 products[fmt] 里的 old_path 改为 new_path 后重写文件。
#[tauri::command]
pub fn relocate_entry(
    registry_path: String,
    ts: String,
    fmt: String,
    old_path: String,
    new_path: String,
) -> Result<(), String> {
    if !Path::new(&new_path).exists() {
        return Err("新路径不存在，未改动".into());
    }
    let reg = Path::new(&registry_path);
    let content = fs::read_to_string(reg).map_err(|e| format!("读取登记文件失败: {e}"))?;
    let mut lines: Vec<String> = content
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| l.to_string())
        .collect();
    let mut hit = false;
    for line in &mut lines {
        let Ok(mut entry) = serde_json::from_str::<RegistryEntry>(line) else {
            continue;
        };
        if entry.ts != ts {
            continue;
        }
        if let Some(paths) = entry.products.get_mut(&fmt) {
            for p in paths.iter_mut() {
                if *p == old_path {
                    *p = new_path.clone();
                    hit = true;
                }
            }
        }
        if hit {
            *line = serde_json::to_string(&entry).map_err(|e| e.to_string())?;
            break;
        }
    }
    if !hit {
        return Err("未找到对应登记行，未改动".into());
    }
    rewrite_jsonl(reg, &lines)
}

/// 「从列表移除」：删除 ts 对应行，重写文件。
#[tauri::command]
pub fn remove_entry(registry_path: String, ts: String) -> Result<(), String> {
    let reg = Path::new(&registry_path);
    let content = fs::read_to_string(reg).map_err(|e| format!("读取登记文件失败: {e}"))?;
    let lines: Vec<String> = content
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter(|l| {
            match serde_json::from_str::<RegistryEntry>(l) {
                Ok(entry) => entry.ts != ts,
                Err(_) => true, // 坏行原样保留，不动作
            }
        })
        .map(|l| l.to_string())
        .collect();
    rewrite_jsonl(reg, &lines)
}

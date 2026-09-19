//! 产物登记处读取/回写：`<输出目录>/_registry.jsonl` 每行一条 JSON。
//! schema 与 pipeline.py `_register_product` 逐字对齐。失败方向=不动作：
//! 单行解析失败跳过该行，绝不丢其他行。
//!
//! 病例 053：产物库语义简化为纯登记制——只读登记文件，不做目录扫描、
//! 不做「重新定位」。用户手动挪/删文件是用户自己的行为，工具不追踪
//! （展示层标「丢失」徽标即可）。

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

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
    /// v2 新增（老记录没有）：VLM 引擎当时的转写模型/思考档
    #[serde(default)]
    pub vlm_model: Option<String>,
    #[serde(default)]
    pub vlm_reasoning: Option<String>,
    /// pipeline 侧新增字段（如 dir_name）随 flatten 原样往返——
    /// 反序列化/再序列化不丢未知字段（病例 052 引入，053 保留）
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
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
    /// 源 PDF 是否仍在原位置（「再次转换」按钮的可用性依据）
    pub source_exists: bool,
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
/// 纯登记制（病例 053）：只读登记文件里记下的路径，不扫描目录。
#[tauri::command]
pub fn read_registry(dirs: Vec<String>) -> Vec<RegistryItem> {
    let mut items = Vec::new();
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
                    }
                }
            }
            let missing = files.iter().any(|f| !f.exists);
            let source_exists = !entry.source_pdf.is_empty() && Path::new(&entry.source_pdf).exists();
            items.push(RegistryItem {
                registry_path: reg.to_string_lossy().into_owned(),
                source_exists,
                entry,
                files,
                missing,
            });
        }
    }
    items.sort_by(|a, b| b.entry.ts.cmp(&a.entry.ts));
    items
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

/// 「从列表移除」：删除 ts 对应行，重写文件。
/// 并发加固（病例 052）：写回前重读 mtime，读改写窗口内被其他实例动过就重读
/// 重试一次；仍冲突则不动作并返回错误（铁律 0：宁可不删，也不错删）。
#[tauri::command]
pub fn remove_entry(registry_path: String, ts: String) -> Result<(), String> {
    let reg = Path::new(&registry_path);
    let mtime_of = |p: &Path| fs::metadata(p).and_then(|m| m.modified()).ok();
    let mut attempt = 0;
    loop {
        let before = mtime_of(reg);
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
        if before.is_some() && mtime_of(reg) != before {
            attempt += 1;
            if attempt < 2 {
                continue; // 读改写窗口内被改写：重读重试一次
            }
            return Err("登记文件正被其他窗口修改，未移除（请重试）".into());
        }
        return rewrite_jsonl(reg, &lines);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tmpdir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("bc-reg-test-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn entry_json(ts: &str, product: &str) -> String {
        serde_json::json!({
            "v": 1,
            "ts": ts,
            "title": format!("书-{ts}"),
            "dir_name": format!("目录-{ts}"), // pipeline 侧字段，Rust 结构未声明
            "source_pdf": "C:/src.pdf",
            "work_dir": "C:/work",
            "engine": "vlm",
            "ocr": true,
            "translate": null,
            "formats": ["epub"],
            "products": {"epub": [product]},
            "elapsed_s": 1.0,
            "app_version": "1.3.9"
        })
        .to_string()
    }

    #[test]
    fn remove_entry_deletes_ts_and_preserves_rest() {
        let dir = tmpdir("remove");
        let reg = dir.join("_registry.jsonl");
        fs::write(
            &reg,
            format!(
                "{}\n{}\n{}\n",
                entry_json("2026-01-01T00:00:00+0800", "C:/a.epub"),
                "这不是合法 JSON——坏行必须原样保留",
                entry_json("2026-01-02T00:00:00+0800", "C:/b.epub"),
            ),
        )
        .unwrap();
        remove_entry(reg.to_string_lossy().into(), "2026-01-01T00:00:00+0800".into()).unwrap();
        let out = fs::read_to_string(&reg).unwrap();
        assert!(!out.contains("2026-01-01T00:00:00"));
        assert!(out.contains("这不是合法 JSON"));
        assert!(out.contains("2026-01-02T00:00:00"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn remove_entry_missing_file_errors() {
        let dir = tmpdir("missing");
        let reg = dir.join("_registry.jsonl");
        assert!(remove_entry(reg.to_string_lossy().into(), "x".into()).is_err());
        let _ = fs::remove_dir_all(&dir);
    }

    /// 病例 053 纯登记制：extra flatten 反序列化→再序列化不丢未知字段
    /// （pipeline 侧写的 dir_name 等原样往返）
    #[test]
    fn extra_roundtrip_preserves_unknown_fields() {
        let line = entry_json("2026-01-05T00:00:00+0800", "C:/a.epub");
        let entry = serde_json::from_str::<RegistryEntry>(&line).unwrap();
        assert_eq!(
            entry.extra.get("dir_name").and_then(|v| v.as_str()),
            Some("目录-2026-01-05T00:00:00+0800")
        );
        let out = serde_json::to_string(&entry).unwrap();
        assert!(out.contains("dir_name"), "extra 字段丢失: {out}");
    }

    /// 纯登记制：read_registry 只呈现登记行内容，目录里未登记的文件不出现
    #[test]
    fn read_registry_is_registration_only() {
        let dir = tmpdir("regonly");
        let product = dir.join("a.epub");
        fs::write(&product, "fake").unwrap();
        fs::write(
            dir.join("_registry.jsonl"),
            format!(
                "{}\n",
                entry_json("2026-01-06T00:00:00+0800", &product.to_string_lossy())
            ),
        )
        .unwrap();
        // 同目录放一个未登记 epub——纯登记制下必须不可见
        fs::write(dir.join("stray.epub"), "stray").unwrap();
        let items = read_registry(vec![dir.to_string_lossy().into_owned()]);
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].files.len(), 1);
        assert!(items[0].files[0].path.ends_with("a.epub"));
        assert!(!items[0].missing);
        let _ = fs::remove_dir_all(&dir);
    }
}

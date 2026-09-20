mod registry;
mod sidecar;

use serde_json::Value;
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

struct AppState {
    /// 任务 id → 子进程 pid（Child 本体由读取线程持有回收）
    children: Mutex<HashMap<String, u32>>,
}

/// 转换日志落点：<repo>/_batch_logs/gui-<书名>-<时间戳>.log（沿用既有目录）
fn open_task_log(pdf: &str) -> Option<std::fs::File> {
    let stem = Path::new(pdf)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "book".into());
    // 文件名安全化：Windows 禁字与空白替换
    let safe: String = stem
        .chars()
        .map(|c| if "<>:\"/\\|?* ".contains(c) { '_' } else { c })
        .take(40)
        .collect();
    let ts = chrono_lite_now();
    let dir = sidecar::repo_root().join("_batch_logs");
    std::fs::create_dir_all(&dir).ok()?;
    std::fs::File::create(dir.join(format!("gui-{safe}-{ts}.log"))).ok()
}

/// 本地时间戳 yyyymmdd-hhmmss（不引 chrono，取 FILETIME 手算太绕——用系统 time 命令格式不如
/// 直接存 unix 秒，可读性够且零依赖）
fn chrono_lite_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}

#[tauri::command]
fn start_conversion(
    app: AppHandle,
    state: State<AppState>,
    id: String,
    pdf: String,
    cli_args: Vec<String>,
    env: HashMap<String, String>,
) -> Result<(), String> {
    let mut log_file = open_task_log(&pdf);
    let mut child = sidecar::spawn_pipeline(&sidecar::SpawnSpec {
        pdf,
        cli_args,
        env,
    })?;
    let pid = child.id();
    state
        .children
        .lock()
        .map_err(|e| e.to_string())?
        .insert(id.clone(), pid);

    let event_name = format!("conversion::{id}");
    let exit_name = format!("conversion::{id}::exit");
    let app2 = app.clone();
    std::thread::spawn(move || {
        let app3 = app2.clone();
        let code = sidecar::run_pipeline(&mut child, move |line| {
            // 落盘失败只丢日志，绝不影响事件转发（失败方向=不动作）
            if let Some(f) = log_file.as_mut() {
                let _ = writeln!(f, "{line}");
            }
            let _ = app3.emit(&event_name, line);
        });
        let _ = app2.emit(
            &exit_name,
            serde_json::json!({"code": code}).to_string(),
        );
        if let Some(state) = app2.try_state::<AppState>() {
            if let Ok(mut map) = state.children.lock() {
                map.remove(&id);
            }
        }
    });
    Ok(())
}

#[tauri::command]
fn cancel_conversion(state: State<AppState>, id: String) -> Result<(), String> {
    let pid = state
        .children
        .lock()
        .map_err(|e| e.to_string())?
        .get(&id)
        .copied();
    match pid {
        Some(p) => {
            sidecar::kill_tree(p);
            Ok(())
        }
        None => Err("任务不存在或已结束".into()),
    }
}

fn settings_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("无法定位配置目录: {e}"))?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建配置目录失败: {e}"))?;
    // 病例 052：debug（tauri dev）与正式包同 identifier 会共用同一份
    // settings.json，多实例整对象互写、最后写入者胜——dev 分叉到独立文件
    let name = if cfg!(debug_assertions) {
        "settings.dev.json"
    } else {
        "settings.json"
    };
    Ok(dir.join(name))
}

#[tauri::command]
fn load_settings(app: AppHandle) -> Result<Option<String>, String> {
    let path = settings_path(&app)?;
    match std::fs::read_to_string(&path) {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            // dev 分叉首次运行：一次性继承正式配置（密钥/提供商免重填），此后读写互不干扰
            if cfg!(debug_assertions) {
                let release = path.with_file_name("settings.json");
                match std::fs::read_to_string(&release) {
                    Ok(s) => Ok(Some(s)),
                    Err(e2) if e2.kind() == std::io::ErrorKind::NotFound => Ok(None),
                    Err(e2) => Err(format!("读取设置失败: {e2}")),
                }
            } else {
                Ok(None)
            }
        }
        Err(e) => Err(format!("读取设置失败: {e}")),
    }
}

/// 聚焦重载数据源（病例 052）：返回 settings 文件的 mtime（毫秒）与内容。
/// 文件不存在 → 两字段 null；读取出错 → Err（前端保持内存，不动作）。
#[tauri::command]
fn read_settings_state(app: AppHandle) -> Result<Value, String> {
    let path = settings_path(&app)?;
    let meta = match std::fs::metadata(&path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(serde_json::json!({"mtime_ms": null, "content": null}));
        }
        Err(e) => return Err(format!("读取设置文件状态失败: {e}")),
    };
    let mtime_ms = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64);
    let content = std::fs::read_to_string(&path).map_err(|e| format!("读取设置失败: {e}"))?;
    Ok(serde_json::json!({"mtime_ms": mtime_ms, "content": content}))
}

/// 设置载入时 historyDirs 清理的存在性判定（病例 052）：返回仍存在的目录子集。
#[tauri::command]
fn existing_dirs(dirs: Vec<String>) -> Vec<String> {
    dirs.into_iter().filter(|d| Path::new(d).is_dir()).collect()
}

#[tauri::command]
fn save_settings(app: AppHandle, json: String) -> Result<(), String> {
    // 先校验是合法 JSON，再落盘（失败方向=不动作）
    serde_json::from_str::<Value>(&json).map_err(|e| format!("设置内容不是合法 JSON: {e}"))?;
    let path = settings_path(&app)?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &json).map_err(|e| format!("写入设置失败: {e}"))?;
    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| format!("替换设置文件失败: {e}"))?;
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("重命名设置文件失败: {e}"))?;
    Ok(())
}

fn http_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs(15))
        .build()
}

fn get_models(base_url: &str, api_key: &str) -> Result<Value, String> {
    let base = base_url.trim_end_matches('/');
    if base.is_empty() {
        return Err("API 地址为空".into());
    }
    let url = format!("{base}/models");
    let mut req = http_agent().get(&url);
    if !api_key.trim().is_empty() {
        req = req.set("Authorization", &format!("Bearer {}", api_key.trim()));
    }
    match req.call() {
        Ok(resp) => resp.into_json::<Value>().map_err(|e| format!("响应解析失败: {e}")),
        Err(ureq::Error::Status(401 | 403, _)) => Err("密钥无效或未开通服务（HTTP 401/403）".into()),
        Err(ureq::Error::Status(429, _)) => Err("触发限流，稍后再试（HTTP 429）".into()),
        Err(ureq::Error::Status(code, _)) => Err(format!("HTTP {code}")),
        Err(e) => Err(format!("网络连接失败，检查网络或代理（{e}）")),
    }
}

#[tauri::command]
async fn test_connection(base_url: String, api_key: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let v = get_models(&base_url, &api_key)?;
        let n = v.get("data").and_then(|d| d.as_array()).map(|a| a.len());
        Ok(match n {
            Some(n) => format!("连接成功 · {n} 个模型"),
            None => "连接成功".into(),
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 「从 /models 拉取」：返回该端点全部型号 id
#[tauri::command]
async fn fetch_models(base_url: String, api_key: String) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let v = get_models(&base_url, &api_key)?;
        let mut ids: Vec<String> = v
            .get("data")
            .and_then(|d| d.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|m| m.get("id").and_then(|s| s.as_str()).map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        ids.sort();
        Ok(ids)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 检查更新：跑 `pipeline.py --check-update`（updater.py 轮询 GitHub release），30s 超时。
/// 解析 stdout 人话行；GitHub 不通时明确报错，不转圈。
#[tauri::command]
async fn check_update() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = sidecar::repo_root();
        let python = sidecar::python_exe(&root);
        let pipeline = sidecar::pipeline_script(&root);
        let mut cmd = std::process::Command::new(python);
        cmd.arg(pipeline)
            .arg("--check-update")
            .current_dir(&root)
            .env("PYTHONIOENCODING", "utf-8")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        let mut child = cmd.spawn().map_err(|e| format!("启动检查进程失败: {e}"))?;
        let pid = child.id();

        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            use std::io::Read;
            let mut buf = String::new();
            if let Some(mut out) = child.stdout.take() {
                let _ = out.read_to_string(&mut buf);
            }
            let code = child.wait().ok().and_then(|s| s.code());
            let _ = tx.send((buf, code));
        });

        match rx.recv_timeout(std::time::Duration::from_secs(30)) {
            Ok((out, _code)) => Ok(parse_check_update_output(&out)),
            Err(_) => {
                sidecar::kill_tree(pid);
                Err("检查更新超时：GitHub 可能不可达，请检查网络或代理".into())
            }
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

fn parse_check_update_output(out: &str) -> Value {
    for line in out.lines() {
        let l = line.trim();
        if l.contains("已是最新版本") {
            return serde_json::json!({"status": "latest"});
        }
        if let Some(rest) = l.strip_prefix("发现新版本") {
            let latest = rest
                .trim_start_matches([':', '：', ' ', 'v'])
                .split(|c: char| !(c.is_ascii_digit() || c == '.'))
                .next()
                .unwrap_or("")
                .to_string();
            let url = out
                .lines()
                .find_map(|x| x.trim().strip_prefix("下载:").map(|u| u.trim().to_string()));
            return serde_json::json!({"status": "update", "latest": latest, "url": url});
        }
        if let Some(err) = l.strip_prefix("检查更新失败") {
            return serde_json::json!({
                "status": "failed",
                "error": err.trim_start_matches([':', '：', ' ']),
            });
        }
    }
    serde_json::json!({"status": "failed", "error": "无法解析检查结果"})
}

/// 默认输出目录建议值：%USERPROFILE%\Documents\BooksConverter
#[tauri::command]
fn default_output_dir() -> String {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_default();
    if home.is_empty() {
        return String::new();
    }
    Path::new(&home)
        .join("Documents")
        .join("BooksConverter")
        .to_string_lossy()
        .into_owned()
}

/// registry/前端来的路径可能带 \\?\ 扩展前缀（如 pipeline 经 canonicalize 的路径），
/// cmd start / explorer 不认，剥掉再交给 shell。
#[cfg(windows)]
fn shell_path(path: &str) -> String {
    path.strip_prefix(r"\\?\").unwrap_or(path).to_string()
}

/// reveal 行为决策（纯函数，可测）：目录 → explorer 直开该目录；
/// 文件 → explorer /select 父目录选中（Explorer 无法 /select 目录——
/// 目标是目录会回落打开「文档」主文件夹，用户实测，病例 057）。
/// 路径归一化：\\?\ 剥离（shell_path）+ 正斜杠转反斜杠（Explorer 认 \）。
enum RevealPlan {
    OpenDir(String),
    Select(String),
}

#[cfg(windows)]
fn reveal_plan(path: &Path) -> RevealPlan {
    let norm = shell_path(&path.to_string_lossy().replace('/', "\\"));
    if path.is_dir() {
        RevealPlan::OpenDir(norm)
    } else {
        RevealPlan::Select(norm)
    }
}

#[tauri::command]
fn reveal_in_explorer(path: String) -> Result<(), String> {
    if !Path::new(&path).exists() {
        return Err("路径不存在".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        match reveal_plan(Path::new(&path)) {
            RevealPlan::OpenDir(dir) => {
                let mut cmd = std::process::Command::new("explorer.exe");
                cmd.arg(dir);
                cmd.spawn().map_err(|e| format!("打开文件夹失败: {e}"))?;
            }
            RevealPlan::Select(p) => {
                // /select 与路径必须分两个 argv 且不引号包裹——
                // `explorer /select,"<path>"` 单参数带引号形态在本机实测
                // 对 ASCII/中文路径一律回落打开「文档」主文件夹
                // （case057 探针 A/C/E 全灭，B/D 两参数形态成功）
                let mut cmd = std::process::Command::new("explorer");
                cmd.arg("/select,").arg(p);
                cmd.creation_flags(0x0800_0000);
                cmd.spawn().map_err(|e| format!("打开资源管理器失败: {e}"))?;
            }
        }
    }
    #[cfg(not(windows))]
    {
        let parent = Path::new(&path).parent().unwrap_or(Path::new(&path));
        open_path(parent)?;
    }
    Ok(())
}

#[cfg(not(windows))]
fn open_path(p: &Path) -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg(p)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_file(path: String) -> Result<(), String> {
    let p = Path::new(&path);
    if !p.exists() {
        return Err("路径不存在".into());
    }
    #[cfg(windows)]
    {
        if p.is_dir() {
            // 目录：cmd /c start 实测不弹可见窗口（case055 探针实证，
            // 进程句柄/标题零变化），改走 explorer.exe 直开
            let mut cmd = std::process::Command::new("explorer.exe");
            cmd.arg(shell_path(&path));
            cmd.spawn().map_err(|e| format!("打开文件夹失败: {e}"))?;
            return Ok(());
        }
        let mut cmd = std::process::Command::new("cmd");
        cmd.args(["/c", "start", "", &shell_path(&path)]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        cmd.spawn().map_err(|e| format!("打开文件失败: {e}"))?;
    }
    #[cfg(not(windows))]
    {
        open_path(Path::new(&path))?;
    }
    Ok(())
}

/// 打开外部链接（手册页的官网链接）：仅放行 http(s)，其他 scheme 不动作
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return Err("仅支持 http(s) 链接".into());
    }
    #[cfg(windows)]
    {
        let mut cmd = std::process::Command::new("cmd");
        cmd.args(["/c", "start", "", &url]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        cmd.spawn().map_err(|e| format!("打开链接失败: {e}"))?;
    }
    #[cfg(not(windows))]
    {
        std::process::Command::new("xdg-open")
            .arg(&url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            children: Mutex::new(HashMap::new()),
        })
        .invoke_handler(tauri::generate_handler![
            start_conversion,
            cancel_conversion,
            registry::read_registry,
            registry::remove_entry,
            load_settings,
            save_settings,
            read_settings_state,
            existing_dirs,
            test_connection,
            fetch_models,
            check_update,
            default_output_dir,
            reveal_in_explorer,
            open_file,
            open_url,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}


#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[cfg(windows)]
    #[test]
    fn reveal_plan_dir_opens_directly() {
        // 目录 → OpenDir（explorer 直开，不再 /select）；正斜杠归一化为反斜杠
        let dir = std::env::temp_dir().join(format!("bc-reveal-{}", std::process::id()));
        fs::create_dir_all(dir.join("sub")).unwrap();
        let fwd = dir.join("sub").to_string_lossy().replace('\\', "/");
        match reveal_plan(Path::new(&fwd)) {
            RevealPlan::OpenDir(p) => {
                assert!(!p.contains('/'), "应归一化为反斜杠: {p}");
                assert!(p.ends_with("sub"), "目标目录本身: {p}");
            }
            RevealPlan::Select(_) => panic!("目录不应走 /select"),
        }
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn reveal_plan_file_selects_in_parent() {
        // 文件 → Select（/select 父目录选中）；\\?\ 前缀剥离
        let dir = std::env::temp_dir().join(format!("bc-reveal-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let f = dir.join("a.epub");
        fs::write(&f, b"x").unwrap();
        let pref = format!(r"\\?\{}", f.to_string_lossy());
        match reveal_plan(Path::new(&pref)) {
            RevealPlan::Select(p) => {
                assert!(!p.starts_with(r"\\?\"), "\\\\?\\ 前缀应剥离: {p}");
                assert!(p.ends_with("a.epub"), "目标是文件本身: {p}");
            }
            RevealPlan::OpenDir(_) => panic!("文件不应直开"),
        }
        let _ = fs::remove_dir_all(&dir);
    }
}

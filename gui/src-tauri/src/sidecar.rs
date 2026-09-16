//! Sidecar：拉起 `pipeline.py --headless` 子进程，逐行转发 stdout JSON 事件。
//! 纯 std::process，不依赖 tauri，可脱离 AppHandle 单测（冒烟 harness 用）。

use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub fn repo_root() -> PathBuf {
    // gui/src-tauri → 仓库根
    let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let canon = base.canonicalize().unwrap_or(base);
    // canonicalize 在 Windows 产出 \\?\ 扩展前缀，cmd/explorer/部分 API 不认——剥掉
    let s = canon.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(stripped) => PathBuf::from(stripped),
        None => canon,
    }
}

pub fn python_exe(root: &Path) -> PathBuf {
    root.join(".venv").join("Scripts").join("python.exe")
}

pub fn pipeline_script(root: &Path) -> PathBuf {
    root.join("pipeline.py")
}

pub struct SpawnSpec {
    pub pdf: String,
    pub cli_args: Vec<String>,
    pub env: HashMap<String, String>,
}

pub fn spawn_pipeline(spec: &SpawnSpec) -> Result<Child, String> {
    let root = repo_root();
    let python = python_exe(&root);
    let pipeline = pipeline_script(&root);
    if !python.exists() {
        return Err(format!("未找到 Python 运行时: {}", python.display()));
    }
    if !pipeline.exists() {
        return Err(format!("未找到管线脚本: {}", pipeline.display()));
    }
    let mut args: Vec<String> = vec![
        pipeline.to_string_lossy().into_owned(),
        spec.pdf.clone(),
    ];
    args.extend(spec.cli_args.iter().cloned());
    if !spec.cli_args.iter().any(|a| a == "--headless") {
        args.push("--headless".into());
    }

    let mut cmd = Command::new(&python);
    cmd.args(&args)
        .current_dir(&root) // cwd=仓库根，config.py 自动读根目录 .env
        .env("PYTHONIOENCODING", "utf-8")
        // 空值不注入：让 .env 兜底
        .envs(spec.env.iter().filter(|(_, v)| !v.trim().is_empty()))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("启动转换进程失败: {e}"))
}

/// 逐行消费子进程输出直至退出：stdout 行原样回调（headless JSON 事件），
/// stderr 行（Python logging）包装成 {"type":"log","line":...} 回调。返回退出码。
pub fn run_pipeline(child: &mut Child, on_line: impl FnMut(String) + Send + 'static) -> Option<i32> {
    let cb = Arc::new(Mutex::new(on_line));
    let stderr_thread = child.stderr.take().map(|err| {
        let cb = Arc::clone(&cb);
        std::thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                let l = line.trim_end();
                if !l.is_empty() {
                    let obj = serde_json::json!({"type": "log", "line": l}).to_string();
                    if let Ok(f) = cb.lock().as_deref_mut() {
                        f(obj);
                    }
                }
            }
        })
    });

    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                if let Ok(f) = cb.lock().as_deref_mut() {
                    f(line);
                }
            }
        }
    }
    let code = child.wait().ok().and_then(|s| s.code());
    if let Some(t) = stderr_thread {
        let _ = t.join();
    }
    code
}

/// 杀进程树（Windows: taskkill /T /F；其余: kill -9 单进程）。
pub fn kill_tree(pid: u32) {
    #[cfg(windows)]
    {
        let mut cmd = Command::new("taskkill");
        cmd.args(["/PID", &pid.to_string(), "/T", "/F"]);
        cmd.creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.status();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("kill")
            .args(["-9", &pid.to_string()])
            .status();
    }
}

//! Sidecar：拉起转换管线子进程，逐行转发 stdout JSON 事件。
//! 纯 std::process，不依赖 tauri，可脱离 AppHandle 单测（冒烟 harness 用）。
//!
//! 两种模式（病例 060，发布机制）：
//! - **发布模式**：主 exe 旁有 `books_converter.exe`（externalBin 落位）→
//!   直接拉起它（透传 `<pdf> <args…> --headless`，事件协议不变）；
//! - **开发模式**：回退仓库 `.venv/Scripts/python.exe pipeline.py`；
//! - 两者都找不到 → 明确报错，不动作（失败方向）。

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

/// 发布模式 sidecar exe 名（externalBin 落主 exe 旁；tauri.conf.json 对应
/// binaries/books_converter）
pub const SIDECAR_EXE_NAME: &str = "books_converter.exe";

/// 管线拉起形态（纯决策，可测）
pub enum PipelinePlan {
    /// 发布模式：books_converter.exe 在侧
    Sidecar { exe: PathBuf },
    /// 开发模式：仓库 .venv + pipeline.py
    Dev {
        python: PathBuf,
        script: PathBuf,
        cwd: PathBuf,
    },
}

/// 决策核心（注入 exe 目录与仓库根，纯文件探测可单测）：
/// exe_dir 旁有 sidecar → 发布模式；否则仓库 .venv + pipeline.py 齐 → 开发模式；
/// 都无 → None（不动作）。
pub fn resolve_pipeline_in(exe_dir: &Path, root: &Path) -> Option<PipelinePlan> {
    let sidecar = exe_dir.join(SIDECAR_EXE_NAME);
    if sidecar.is_file() {
        return Some(PipelinePlan::Sidecar { exe: sidecar });
    }
    let python = python_exe(root);
    let script = pipeline_script(root);
    if python.is_file() && script.is_file() {
        return Some(PipelinePlan::Dev {
            python,
            script,
            cwd: root.to_path_buf(),
        });
    }
    None
}

/// 线上入口：以当前进程 exe 目录为发布探测点，编译期仓库根为开发回退。
/// `BC_FORCE_DEV_PIPELINE=1` 强制开发模式——dev 实例必须跑仓库最新源码
/// （AGENTS 交付义务），而 tauri dev 会把 externalBin 拷进 target/debug，
/// 不加这个开关 dev 会静默走发布模式（假最新）。
pub fn resolve_pipeline() -> Option<PipelinePlan> {
    if std::env::var("BC_FORCE_DEV_PIPELINE").is_ok() {
        let root = repo_root();
        let python = python_exe(&root);
        let script = pipeline_script(&root);
        if python.is_file() && script.is_file() {
            return Some(PipelinePlan::Dev {
                python,
                script,
                cwd: root,
            });
        }
        return None;
    }
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();
    resolve_pipeline_in(&exe_dir, &repo_root())
}

pub struct SpawnSpec {
    pub pdf: String,
    pub cli_args: Vec<String>,
    pub env: HashMap<String, String>,
}

/// 两种模式共用的进程装配：参数（pdf + cli_args + --headless）、cwd、
/// env 注入（空值不注入，让 .env/默认值兜底）、stdout/stderr 管道。
fn spawn_with(
    prog: &Path,
    prefix_args: Vec<String>,
    cwd: &Path,
    spec: &SpawnSpec,
) -> Result<Child, String> {
    let mut args: Vec<String> = prefix_args;
    args.push(spec.pdf.clone());
    args.extend(spec.cli_args.iter().cloned());
    if !spec.cli_args.iter().any(|a| a == "--headless") {
        args.push("--headless".into());
    }

    let mut cmd = Command::new(prog);
    cmd.args(&args)
        .current_dir(cwd)
        .env("PYTHONIOENCODING", "utf-8")
        // 空值不注入：让 .env 兜底
        .envs(spec.env.iter().filter(|(_, v)| !v.trim().is_empty()))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn().map_err(|e| format!("启动转换进程失败: {e}"))
}

pub fn spawn_pipeline(spec: &SpawnSpec) -> Result<Child, String> {
    match resolve_pipeline() {
        Some(PipelinePlan::Sidecar { exe }) => {
            // 发布模式：直拉 sidecar exe（cwd=exe 目录，frozen config.py 在
            // 该处读 .env——绿色 zip 用户可把 .env 放 exe 旁）
            let cwd = exe.parent().map(|p| p.to_path_buf()).unwrap_or_default();
            spawn_with(&exe, vec![], &cwd, spec)
        }
        Some(PipelinePlan::Dev {
            python,
            script,
            cwd,
        }) => {
            // 开发模式：仓库 .venv + pipeline.py（cwd=仓库根，config.py
            // 自动读根目录 .env）
            spawn_with(
                &python,
                vec![script.to_string_lossy().into_owned()],
                &cwd,
                spec,
            )
        }
        None => Err(format!(
            "未找到转换引擎：程序目录无 {}，开发仓库 .venv/pipeline.py 也不可用",
            SIDECAR_EXE_NAME
        )),
    }
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


#[cfg(test)]
mod tests {
    use super::*;

    fn sandbox(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("bc-sidecar-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn sidecar_exe_wins_release_mode() {
        // exe 旁有 books_converter.exe → 发布模式（即使开发仓库也可用）
        let dir = sandbox("release");
        let exe = dir.join(SIDECAR_EXE_NAME);
        std::fs::write(&exe, b"fake").unwrap();
        let root = sandbox("release-repo");
        std::fs::create_dir_all(root.join(".venv/Scripts")).unwrap();
        std::fs::write(root.join(".venv/Scripts/python.exe"), b"x").unwrap();
        std::fs::write(root.join("pipeline.py"), b"x").unwrap();
        match resolve_pipeline_in(&dir, &root) {
            Some(PipelinePlan::Sidecar { exe: e }) => assert_eq!(e, exe),
            _ => panic!("应走发布模式"),
        }
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn falls_back_to_dev_mode() {
        // exe 旁无 sidecar、仓库 .venv + pipeline.py 齐 → 开发模式
        let dir = sandbox("dev-exe");
        let root = sandbox("dev-repo");
        std::fs::create_dir_all(root.join(".venv/Scripts")).unwrap();
        std::fs::write(root.join(".venv/Scripts/python.exe"), b"x").unwrap();
        std::fs::write(root.join("pipeline.py"), b"x").unwrap();
        match resolve_pipeline_in(&dir, &root) {
            Some(PipelinePlan::Dev { python, script, cwd }) => {
                assert!(python.ends_with("python.exe"));
                assert!(script.ends_with("pipeline.py"));
                assert_eq!(cwd, root);
            }
            _ => panic!("应走开发模式"),
        }
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn neither_found_is_none_no_action() {
        // 两边都没有 → None（不动作）；缺一边也 None
        let dir = sandbox("none-exe");
        let root = sandbox("none-repo");
        assert!(resolve_pipeline_in(&dir, &root).is_none());
        // 只有 python 没有 pipeline.py → 仍 None（半个开发环境不算数）
        std::fs::create_dir_all(root.join(".venv/Scripts")).unwrap();
        std::fs::write(root.join(".venv/Scripts/python.exe"), b"x").unwrap();
        assert!(resolve_pipeline_in(&dir, &root).is_none());
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&root);
    }
}

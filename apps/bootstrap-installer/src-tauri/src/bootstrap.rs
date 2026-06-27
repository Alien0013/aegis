use std::process::Command;

use serde::Serialize;

use crate::install_script::{current_plan, InstallPlan};

#[derive(Debug, Clone, Serialize)]
pub struct InstallResult {
    pub ok: bool,
    pub log: Vec<String>,
    pub error: Option<String>,
}

#[tauri::command]
pub fn bootstrap_plan() -> InstallPlan {
    current_plan()
}

#[tauri::command]
pub fn run_bootstrap_install() -> InstallResult {
    let plan = current_plan();
    let mut parts = plan.command.split_whitespace();
    let Some(program) = parts.next() else {
        return InstallResult {
            ok: false,
            log: vec![],
            error: Some("install command is empty".into()),
        };
    };
    let output = Command::new(program).args(parts).output();
    match output {
        Ok(out) if out.status.success() => InstallResult {
            ok: true,
            log: String::from_utf8_lossy(&out.stdout)
                .lines()
                .map(str::to_owned)
                .collect(),
            error: None,
        },
        Ok(out) => InstallResult {
            ok: false,
            log: String::from_utf8_lossy(&out.stdout)
                .lines()
                .map(str::to_owned)
                .collect(),
            error: Some(String::from_utf8_lossy(&out.stderr).trim().to_owned()),
        },
        Err(err) => InstallResult {
            ok: false,
            log: vec![],
            error: Some(err.to_string()),
        },
    }
}

#[tauri::command]
pub fn open_update_docs() -> crate::update::UpdateHint {
    crate::update::update_hint()
}

use std::path::PathBuf;

use serde::Serialize;

use crate::paths::shell_installer;
use crate::powershell::powershell_installer;

#[derive(Debug, Clone, Serialize)]
pub struct InstallPlan {
    pub platform: &'static str,
    pub script: &'static str,
    pub command: String,
    pub update_command: &'static str,
    pub setup_command: &'static str,
}

pub fn current_plan() -> InstallPlan {
    if cfg!(windows) {
        let script: PathBuf = powershell_installer();
        InstallPlan {
            platform: "windows",
            script: "install.ps1",
            command: format!(
                "powershell -ExecutionPolicy Bypass -File {}",
                script.display()
            ),
            update_command: "aegis update",
            setup_command: "aegis setup",
        }
    } else {
        let script = shell_installer();
        InstallPlan {
            platform: if cfg!(target_os = "macos") {
                "macos"
            } else {
                "linux"
            },
            script: "install.sh",
            command: format!("bash {}", script.display()),
            update_command: "aegis update",
            setup_command: "aegis setup",
        }
    }
}

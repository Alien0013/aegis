use std::path::PathBuf;

use crate::paths;

pub const POWERSHELL_INSTALLER_NAME: &str = "install.ps1";

pub fn powershell_installer() -> PathBuf {
    paths::powershell_installer()
}

pub fn powershell_command() -> String {
    format!(
        "powershell -ExecutionPolicy Bypass -File {}",
        powershell_installer().display()
    )
}

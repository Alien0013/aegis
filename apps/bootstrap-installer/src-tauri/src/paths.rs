use std::path::PathBuf;

pub fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

pub fn shell_installer() -> PathBuf {
    repo_root().join("install.sh")
}

pub fn powershell_installer() -> PathBuf {
    repo_root().join("install.ps1")
}

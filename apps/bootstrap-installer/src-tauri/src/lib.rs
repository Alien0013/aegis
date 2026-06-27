pub mod bootstrap;
pub mod events;
pub mod install_script;
pub mod paths;
pub mod powershell;
pub mod update;

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            bootstrap::bootstrap_plan,
            bootstrap::run_bootstrap_install,
            bootstrap::open_update_docs,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run AEGIS bootstrap installer");
}

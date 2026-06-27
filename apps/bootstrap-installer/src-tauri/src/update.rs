use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct UpdateHint {
    pub command: &'static str,
    pub docs: &'static str,
}

pub fn update_hint() -> UpdateHint {
    UpdateHint {
        command: "aegis update",
        docs: "Run aegis update --check after installation.",
    }
}

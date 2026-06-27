use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct InstallEvent {
    pub level: &'static str,
    pub message: String,
}

impl InstallEvent {
    pub fn info(message: impl Into<String>) -> Self {
        Self {
            level: "info",
            message: message.into(),
        }
    }

    pub fn error(message: impl Into<String>) -> Self {
        Self {
            level: "error",
            message: message.into(),
        }
    }
}

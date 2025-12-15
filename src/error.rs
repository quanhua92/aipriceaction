use thiserror::Error as ThisError;

#[derive(ThisError, Debug)]
pub enum AppError {
    #[error("Configuration error: {0}")]
    Config(String),

    #[error("IO error: {0}")]
    Io(String),

    #[error("Network error: {0}")]
    Network(String),

    #[error("Parse error: {0}")]
    Parse(String),

    #[error("Invalid input: {0}")]
    InvalidInput(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Skip ticker: {0}")]
    SkipTicker(String),

    #[error("Rate limit exceeded")]
    RateLimit,

    #[error("Database error: {0}")]
    Database(String),

    #[error("{0}")]
    Other(String),
}

impl From<tokio::io::Error> for AppError {
    fn from(err: tokio::io::Error) -> Self {
        AppError::Io(err.to_string())
    }
}

impl From<csv::Error> for AppError {
    fn from(err: csv::Error) -> Self {
        AppError::Io(format!("CSV error: {}", err))
    }
}

impl From<sqlx::Error> for AppError {
    fn from(err: sqlx::Error) -> Self {
        AppError::Database(err.to_string())
    }
}

pub type Result<T> = std::result::Result<T, AppError>;

// Alias for convenience
pub type Error = AppError;

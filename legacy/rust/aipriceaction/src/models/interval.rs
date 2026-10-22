use std::fmt;
use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Interval {
    Daily,  // CSV: 1D.csv  → DB: '1D'
    Hourly, // CSV: 1H.csv  → DB: '1h'
    Minute, // CSV: 1m.csv  → DB: '1m'
}

impl Interval {
    /// The exact string stored in the DB `interval` column.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Daily => "1D",
            Self::Hourly => "1h",
            Self::Minute => "1m",
        }
    }

    /// Parse from CSV filename stem: "1D"→Daily, "1H"→Hourly, "1m"→Minute
    pub fn from_filename(path: &Path) -> Result<Self, String> {
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .ok_or("cannot extract filename")?;
        match stem {
            "1D" => Ok(Self::Daily),
            "1H" => Ok(Self::Hourly),
            "1m" => Ok(Self::Minute),
            other => Err(format!("unknown interval: {other}")),
        }
    }

    /// Parse from a user-supplied string (CLI argument): "1D"/"1d"→Daily, etc.
    pub fn from_arg(s: &str) -> Result<Self, String> {
        match s.to_uppercase().as_str() {
            "1D" => Ok(Self::Daily),
            "1H" => Ok(Self::Hourly),
            "1M" => Ok(Self::Minute),
            other => Err(format!("unknown interval: {other}")),
        }
    }
}

impl fmt::Display for Interval {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

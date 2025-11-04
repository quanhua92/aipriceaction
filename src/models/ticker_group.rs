use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// Ticker groups organized by sector/category
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TickerGroups {
    #[serde(flatten)]
    pub groups: HashMap<String, Vec<String>>,
}

impl TickerGroups {
    /// Load ticker groups from JSON file
    pub fn from_file<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error>> {
        let content = fs::read_to_string(path)?;
        let groups: HashMap<String, Vec<String>> = serde_json::from_str(&content)?;
        Ok(Self { groups })
    }

    /// Load ticker groups from the default location (ticker_group.json in project root)
    pub fn load_default() -> Result<Self, Box<dyn std::error::Error>> {
        Self::from_file("ticker_group.json")
    }

    /// Get all tickers across all groups (flattened)
    pub fn all_tickers(&self) -> Vec<String> {
        let mut tickers: Vec<String> = self
            .groups
            .values()
            .flat_map(|v| v.clone())
            .collect();
        tickers.sort();
        tickers.dedup();
        tickers
    }

    /// Get tickers for a specific group
    pub fn get_group(&self, group_name: &str) -> Option<&Vec<String>> {
        self.groups.get(group_name)
    }

    /// Get all group names
    pub fn group_names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.groups.keys().cloned().collect();
        names.sort();
        names
    }

    /// Get the number of groups
    pub fn group_count(&self) -> usize {
        self.groups.len()
    }

    /// Get the total number of unique tickers
    pub fn ticker_count(&self) -> usize {
        self.all_tickers().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ticker_groups_structure() {
        let mut groups = HashMap::new();
        groups.insert("TECH".to_string(), vec!["FPT".to_string(), "CMG".to_string()]);
        groups.insert("BANK".to_string(), vec!["VCB".to_string(), "TCB".to_string()]);

        let ticker_groups = TickerGroups { groups };

        assert_eq!(ticker_groups.group_count(), 2);
        assert_eq!(ticker_groups.ticker_count(), 4);
        assert_eq!(ticker_groups.group_names(), vec!["BANK", "TECH"]);
    }
}

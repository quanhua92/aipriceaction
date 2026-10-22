use std::collections::{BTreeMap, HashMap};

/// Resolve a data file by searching CWD then parent directory.
pub(crate) fn resolve_data_file(name: &str) -> Result<std::path::PathBuf, Box<dyn std::error::Error + Send + Sync>> {
    let cwd = std::path::Path::new(name);
    if cwd.exists() {
        return Ok(cwd.to_path_buf());
    }
    let parent = std::path::Path::new("..").join(name);
    if parent.exists() {
        return Ok(parent);
    }
    Err(format!("Data file not found: {name} (searched . and ../)").into())
}

/// Load vn.csv rows as a HashMap keyed by symbol (uppercase).
pub(crate) fn load_vn_csv() -> Result<HashMap<String, serde_json::Value>, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file("vn.csv")?;
    let content = std::fs::read_to_string(&path)?;
    let mut map = HashMap::new();

    let mut rdr = csv::Reader::from_reader(content.as_bytes());
    for result in rdr.records() {
        let record = result?;
        let symbol = record.get(0).unwrap_or("").trim();
        if symbol.is_empty() {
            continue;
        }
        let organ_name = record.get(1).unwrap_or("").trim();
        let en_organ_name = record.get(2).unwrap_or("").trim();
        let exchange = record.get(3).unwrap_or("").trim();
        let stock_type = record.get(4).unwrap_or("").trim();
        let val = serde_json::json!({
            "ticker": symbol,
            "organ_name": organ_name,
            "en_organ_name": en_organ_name,
            "exchange": exchange,
            "type": stock_type,
        });
        map.insert(symbol.to_uppercase(), val);
    }

    Ok(map)
}

/// Load company_info.json array.
pub(crate) fn load_company_info() -> Result<Vec<serde_json::Value>, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file("company_info.json")?;
    let content = std::fs::read_to_string(&path)?;
    let data: Vec<serde_json::Value> = serde_json::from_str(&content)?;
    Ok(data)
}

/// Merge vn.csv baseline with company_info.json details.
/// Only tickers present in both vn.csv and company_info.json are included.
/// vn.csv provides name/exchange; company_info.json adds profile + financial_ratios.
pub(crate) fn load_merged_info() -> Result<Vec<serde_json::Value>, Box<dyn std::error::Error + Send + Sync>> {
    let vn_map = load_vn_csv()
        .map_err(|e| {
            tracing::warn!("vn.csv not available: {e}");
        })
        .unwrap_or_default();

    let company_entries = load_company_info()
        .map_err(|e| {
            tracing::warn!("company_info.json not available: {e}");
        })
        .unwrap_or_default();

    let mut result = Vec::new();

    for entry in &company_entries {
        let ticker = entry
            .get("ticker")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .to_uppercase();

        let mut merged = if let Some(base) = vn_map.get(&ticker) {
            base.clone()
        } else {
            // Not in vn.csv — skip this entry
            continue;
        };

        // Merge company_info fields into base (company fields take precedence)
        if let Some(obj) = entry.as_object() {
            if let Some(merged_obj) = merged.as_object_mut() {
                for (key, val) in obj {
                    merged_obj.insert(key.clone(), val.clone());
                }
            }
        }

        result.push(merged);
    }

    result.sort_by(|a, b| {
        a.get("ticker")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .cmp(b.get("ticker").and_then(|t| t.as_str()).unwrap_or(""))
    });
    Ok(result)
}

// ── Group file loaders ──

pub(crate) fn load_vn_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file("ticker_group.json")?;
    let content = std::fs::read_to_string(&path)?;
    let groups: BTreeMap<String, Vec<String>> = serde_json::from_str(&content)?;
    Ok(groups)
}

pub(crate) fn load_crypto_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file("binance_tickers.json")?;
    let content = std::fs::read_to_string(&path)?;

    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let symbols: Vec<String> = raw["data"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|item| item["symbol"].as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let mut map = BTreeMap::new();
    map.insert("CRYPTO_TOP_100".to_string(), symbols);
    Ok(map)
}

pub(crate) fn load_yahoo_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let mut map = load_groups_from_source("global")?;

    // Merge additional sources (e.g. SJC)
    for source in crate::constants::MERGE_WITH_YAHOO {
        let extra = load_groups_from_source(source)?;
        for (category, symbols) in extra {
            map.entry(category).or_insert_with(Vec::new).extend(symbols);
        }
    }

    Ok(map)
}

pub(crate) fn load_groups_from_source(source: &str) -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    let filename = format!("{source}_tickers.json");
    let path = resolve_data_file(&filename)?;
    let content = std::fs::read_to_string(&path)?;

    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let mut map = BTreeMap::new();
    if let Some(data) = raw["data"].as_array() {
        for item in data {
            let (symbol, category) = match (item["symbol"].as_str(), item["category"].as_str()) {
                (Some(s), Some(c)) => (s.to_string(), c.to_string()),
                (Some(s), None) => (s.to_string(), "Other".to_string()),
                _ => continue,
            };
            map.entry(category).or_insert_with(Vec::new).push(symbol);
        }
    }
    Ok(map)
}

/// Merge groups from all sources (vn > yahoo > crypto priority on key conflicts).
pub(crate) fn load_all_groups() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>> {
    type LoadFn = fn() -> Result<BTreeMap<String, Vec<String>>, Box<dyn std::error::Error + Send + Sync>>;
    let load_fns: [LoadFn; 3] = [load_vn_groups, load_yahoo_groups, load_crypto_groups];
    let mut merged = BTreeMap::new();
    for load_fn in load_fns {
        let groups = load_fn()?;
        for (k, v) in groups {
            merged.entry(k).or_insert(v);
        }
    }
    Ok(merged)
}

// ── Name loaders ──

pub(crate) fn load_vn_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> {
    // Load valid tickers from ticker_group.json
    let path = resolve_data_file("ticker_group.json")?;
    let content = std::fs::read_to_string(&path)?;
    let groups: BTreeMap<String, Vec<String>> = serde_json::from_str(&content)?;
    let valid_tickers: std::collections::HashSet<String> = groups
        .values()
        .flat_map(|v| v.iter().cloned())
        .collect();

    // Load names from vn.csv, only keeping valid tickers
    let vn_map = load_vn_csv()?;
    Ok(vn_map
        .into_iter()
        .filter(|(ticker, _)| valid_tickers.contains(ticker))
        .filter_map(|(ticker, val)| {
            val.get("organ_name")
                .and_then(|v| v.as_str())
                .map(|name| (ticker, name.to_string()))
        })
        .collect())
}

pub(crate) fn load_names_from_file(filename: &str) -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> {
    let path = resolve_data_file(filename)?;
    let content = std::fs::read_to_string(&path)?;
    let raw: serde_json::Value = serde_json::from_str(&content)?;

    let mut names = BTreeMap::new();
    if let Some(data) = raw["data"].as_array() {
        for item in data {
            if let (Some(symbol), Some(name)) = (item["symbol"].as_str(), item["name"].as_str()) {
                names.insert(symbol.to_string(), name.to_string());
            }
        }
    }
    Ok(names)
}

pub(crate) fn load_crypto_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> {
    load_names_from_file("binance_tickers.json")
}

pub(crate) fn load_yahoo_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> {
    let mut names = load_names_from_file("global_tickers.json")?;

    // Merge additional sources (e.g. SJC)
    for source in crate::constants::MERGE_WITH_YAHOO {
        let extra = load_names_from_file(&format!("{source}_tickers.json"))?;
        for (symbol, name) in extra {
            names.entry(symbol).or_insert(name);
        }
    }

    Ok(names)
}

/// Merge names from all sources (vn > yahoo > crypto priority on symbol conflicts).
pub(crate) fn load_all_names() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>> {
    type LoadFn = fn() -> Result<BTreeMap<String, String>, Box<dyn std::error::Error + Send + Sync>>;
    let load_fns: [LoadFn; 3] = [load_vn_names, load_yahoo_names, load_crypto_names];
    let mut merged = BTreeMap::new();
    for load_fn in load_fns {
        let names = load_fn()?;
        for (k, v) in names {
            merged.entry(k).or_insert(v);
        }
    }
    Ok(merged)
}

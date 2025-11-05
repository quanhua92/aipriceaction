mod csv_parser;
mod importer;
pub mod market_stats;
pub mod vci;
pub mod ticker_fetcher;
pub mod data_sync;
pub mod csv_enhancer;
pub mod csv_validator;
pub mod data_store;

pub use importer::import_legacy;
pub use market_stats::{get_market_stats, get_ticker_info, is_index, MarketStats, TickerInfo, TimeframeInfo};
pub use vci::{VciClient, VciError, OhlcvData, CompanyInfo};
pub use ticker_fetcher::TickerFetcher;
pub use data_sync::DataSync;
pub use csv_enhancer::{enhance_interval, EnhancementStats};
pub use csv_validator::{validate_and_repair_interval, CorruptionReport};
pub use data_store::{DataStore, SharedDataStore, HealthStats, SharedHealthStats, estimate_memory_usage};

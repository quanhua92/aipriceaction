use chrono::NaiveDate;
use serde::Deserialize;

use crate::constants::sjc_worker;

/// A single price record from the SJC API.
#[derive(Debug, Clone, Deserialize)]
pub struct SjcApiRecord {
    #[serde(rename = "BranchName")]
    pub branch_name: String,
    #[serde(rename = "BuyValue")]
    pub buy_value: f64,
    #[serde(rename = "SellValue")]
    pub sell_value: f64,
}

#[derive(Debug, Clone, Deserialize)]
struct SjcApiResponse {
    success: bool,
    data: Vec<SjcApiRecord>,
}

/// SJC gold price fetched from the API (single branch).
#[derive(Debug, Clone)]
pub struct SjcPriceRecord {
    pub buy: f64,
    pub sell: f64,
}

/// SJC gold price provider — fetches live gold prices from sjc.com.vn.
pub struct SjcProvider {
    client: reqwest::Client,
    branch: String,
}

impl SjcProvider {
    /// Create a new SJC provider.
    pub fn new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let client = reqwest::Client::builder()
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            .referer(true)
            .default_headers({
                let mut headers = reqwest::header::HeaderMap::new();
                headers.insert(reqwest::header::REFERER, reqwest::header::HeaderValue::from_static("https://sjc.com.vn/"));
                headers
            })
            .build()?;
        Ok(Self {
            client,
            branch: sjc_worker::BRANCH.to_string(),
        })
    }

    /// Fetch gold prices for a specific date (or today if None).
    ///
    /// Uses the working PriceService.ashx endpoint matching gold-price.py.
    pub async fn fetch_price(
        &self,
        date: Option<NaiveDate>,
    ) -> Result<SjcPriceRecord, Box<dyn std::error::Error + Send + Sync>> {
        let target_date = date.unwrap_or_else(|| chrono::Local::now().date_naive());
        let formatted_date = target_date.format("%d/%m/%Y").to_string();

        let payload = format!("method=GetSJCGoldPriceByDate&toDate={formatted_date}");

        let resp = self
            .client
            .post(sjc_worker::API_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body(payload)
            .send()
            .await?
            .error_for_status()?
            .json::<SjcApiResponse>()
            .await?;

        if !resp.success {
            return Err("SJC API returned success=false".into());
        }

        // Find the matching branch
        let record = resp
            .data
            .iter()
            .find(|r| r.branch_name == self.branch)
            .ok_or_else(|| format!("Branch '{}' not found in SJC API response", self.branch))?;

        Ok(SjcPriceRecord {
            buy: record.buy_value,
            sell: record.sell_value,
        })
    }

    /// Convenience: fetch today's gold price.
    pub async fn fetch_today(&self) -> Result<SjcPriceRecord, Box<dyn std::error::Error + Send + Sync>> {
        self.fetch_price(None).await
    }
}

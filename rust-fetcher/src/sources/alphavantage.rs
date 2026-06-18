//! Alpha Vantage `TIME_SERIES_DAILY` fetcher.
//!
//! Free tier: 25 calls/day. We don't enforce the budget here —
//! the orchestrator owns the rate-limit state (see `state.rs`) so
//! the same accounting applies regardless of which source got
//! the call.
//!
//! Endpoint:
//!   `https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=...&apikey=...`
//!
//! Response shape (relevant subset):
//! ```json
//! {
//!   "Meta Data": { ... },
//!   "Time Series (Daily)": {
//!     "2026-06-18": { "1. open": "...", "2. high": "...",
//!                     "3. low": "...", "4. close": "...",
//!                     "5. volume": "..." },
//!     ...
//!   }
//! }
//! ```
//!
//! Throttle responses come back HTTP 200 with a `Note` or
//! `Information` key instead of `Time Series (Daily)`. Same shape
//! as `NEWS_SENTIMENT` — we detect and return an empty vec.

use crate::db::PriceRow;
use crate::sources::Source;
use anyhow::Result;
use async_trait::async_trait;
use serde_json::Value;

pub struct AlphaVantage {
    api_key: String,
    client:  reqwest::Client,
    base:    String,
}

impl AlphaVantage {
    pub fn new(api_key: impl Into<String>) -> Self {
        Self {
            api_key: api_key.into(),
            client:  reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(20))
                .build()
                .expect("reqwest client"),
            base: "https://www.alphavantage.co".to_string(),
        }
    }

    /// Test hook: override the base URL so wiremock can intercept.
    #[doc(hidden)]
    pub fn with_base(mut self, base: impl Into<String>) -> Self {
        self.base = base.into();
        self
    }

    /// Parse a response JSON into rows. Public for the offline test
    /// (which feeds a canned response rather than hitting wiremock
    /// for the simple parser cases).
    pub fn parse_response(symbol: &str, fetched_at: &str, json: &Value) -> Vec<PriceRow> {
        let Some(series) = json.get("Time Series (Daily)").and_then(Value::as_object) else {
            return vec![];
        };
        let mut rows = Vec::with_capacity(series.len());
        for (date, fields) in series {
            let Some(fields) = fields.as_object() else { continue };
            let f = |k: &str| -> Option<f64> {
                fields.get(k)
                    .and_then(Value::as_str)
                    .and_then(|s| s.parse().ok())
            };
            let vol = fields.get("5. volume")
                .and_then(Value::as_str)
                .and_then(|s| s.parse::<i64>().ok());
            rows.push(PriceRow {
                fetched_at: fetched_at.to_string(),
                symbol:     symbol.to_string(),
                source:     "alphavantage".to_string(),
                timestamp:  format!("{date}T00:00:00+00:00"),
                interval:   "1d".to_string(),
                open:       f("1. open"),
                high:       f("2. high"),
                low:        f("3. low"),
                close:      f("4. close"),
                volume:     vol,
                vwap:       None,
                change_pct: None,
                extra:      None,
            });
        }
        rows
    }
}

#[async_trait]
impl Source for AlphaVantage {
    fn name(&self) -> &'static str {
        "alphavantage"
    }

    async fn fetch_daily(&self, symbol: &str) -> Result<Vec<PriceRow>> {
        let url = format!("{}/query", self.base);
        let resp = self.client.get(&url)
            .query(&[
                ("function", "TIME_SERIES_DAILY"),
                ("symbol",   symbol),
                ("apikey",   self.api_key.as_str()),
            ])
            .send()
            .await?;

        if !resp.status().is_success() {
            tracing::warn!(
                source = "alphavantage", symbol,
                status = %resp.status(),
                "non-2xx response — treating as no data",
            );
            return Ok(vec![]);
        }

        let json: Value = resp.json().await?;

        // Throttle / informational responses have no time series.
        if json.get("Note").is_some() || json.get("Information").is_some() {
            tracing::warn!(
                source = "alphavantage", symbol,
                note = ?json.get("Note").or_else(|| json.get("Information")),
                "alpha vantage throttled — treating as no data",
            );
            return Ok(vec![]);
        }

        let fetched_at = chrono_like_now();
        Ok(Self::parse_response(symbol, &fetched_at, &json))
    }
}

/// Tiny RFC3339-ish "now" without pulling in `chrono`. The collector
/// only consumes this for the `fetched_at` audit column; format
/// compatibility with Python is "ISO-8601 with timezone" and that's
/// what we produce.
fn chrono_like_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Days since epoch (rough) + HH:MM:SS via integer math. We
    // could pull chrono in for prettier output but the audit column
    // doesn't need calendar correctness, just a stable timestamp.
    let days  = secs / 86_400;
    let s_day = secs % 86_400;
    let (h, rem) = (s_day / 3600, s_day % 3600);
    let (m, s)   = (rem / 60, rem % 60);
    // 1970-01-01 + days, computed naively.
    let y0 = 1970;
    let mut y = y0;
    let mut d = days as i64;
    loop {
        let leap = (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0);
        let ydays = if leap { 366 } else { 365 };
        if d < ydays { break; }
        d -= ydays;
        y += 1;
    }
    let leap = (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0);
    let months = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut mo = 1;
    for &md in &months {
        if d < md { break; }
        d -= md;
        mo += 1;
    }
    let day = d + 1;
    format!("{y:04}-{mo:02}-{day:02}T{h:02}:{m:02}:{s:02}+00:00")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_two_day_series() {
        let body = json!({
            "Meta Data": {},
            "Time Series (Daily)": {
                "2026-06-18": {
                    "1. open": "200.0", "2. high": "205.0",
                    "3. low": "199.0", "4. close": "203.5",
                    "5. volume": "12345678",
                },
                "2026-06-17": {
                    "1. open": "198.0", "2. high": "201.0",
                    "3. low": "197.0", "4. close": "200.0",
                    "5. volume": "10000000",
                },
            },
        });
        let rows = AlphaVantage::parse_response(
            "AAPL", "2026-06-19T00:00:00+00:00", &body,
        );
        assert_eq!(rows.len(), 2);
        let day_18 = rows.iter().find(|r| r.timestamp.starts_with("2026-06-18")).unwrap();
        assert_eq!(day_18.close, Some(203.5));
        assert_eq!(day_18.volume, Some(12_345_678));
        assert_eq!(day_18.source, "alphavantage");
        assert_eq!(day_18.interval, "1d");
    }

    #[test]
    fn missing_series_yields_empty() {
        let body = json!({"Note": "Thank you for using Alpha Vantage..."});
        assert!(AlphaVantage::parse_response("AAPL", "now", &body).is_empty());
    }

    #[test]
    fn bad_numbers_become_none_not_panic() {
        let body = json!({
            "Time Series (Daily)": {
                "2026-06-18": {
                    "1. open": "garbage",
                    "4. close": "203.5",
                    "5. volume": "not-a-number",
                },
            },
        });
        let rows = AlphaVantage::parse_response("AAPL", "now", &body);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].open, None);
        assert_eq!(rows[0].close, Some(203.5));
        assert_eq!(rows[0].volume, None);
    }
}

//! Per-provider fetchers. One module per source; each implements the
//! `Source` trait so the orchestrator can drive them through a
//! uniform interface.
//!
//! New sources land here as additional `pub mod` lines + a match arm
//! in the CLI's source resolver.

use crate::db::PriceRow;
use crate::rate_limit::RateLimit;
use anyhow::Result;
use async_trait::async_trait;

pub mod alphavantage;

/// Common surface every source implements. `fetch_daily` is the
/// minimum viable verb — historical daily bars for one symbol. More
/// verbs (intraday, fundamentals) can be added later without
/// invalidating this one.
#[async_trait]
pub trait Source: Send + Sync {
    /// Human-friendly name used in logs and as the `source` column.
    fn name(&self) -> &'static str;

    /// Per-source default rate limit. Defaults to a generous `None`
    /// (no limit) so a hand-rolled source for testing doesn't need
    /// to think about it. Real sources should override with the
    /// documented free-tier cap — e.g. `per_minute(60)` for Finnhub.
    fn default_rate_limit(&self) -> Option<RateLimit> { None }

    /// Fetch daily OHLCV for one symbol. Returns rows ready for
    /// `Db::insert_batch`. Empty vec = "no data" (e.g. delisted),
    /// not an error.
    async fn fetch_daily(&self, symbol: &str) -> Result<Vec<PriceRow>>;
}

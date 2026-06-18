//! Per-provider fetchers. One module per source; each implements the
//! `Source` trait so the orchestrator can drive them through a
//! uniform interface.
//!
//! New sources land here as additional `pub mod` lines + a match arm
//! in the CLI's source resolver.

use crate::db::PriceRow;
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

    /// Fetch daily OHLCV for one symbol. Returns rows ready for
    /// `Db::insert_batch`. Empty vec = "no data" (e.g. delisted),
    /// not an error.
    async fn fetch_daily(&self, symbol: &str) -> Result<Vec<PriceRow>>;
}

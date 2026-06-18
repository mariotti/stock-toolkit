//! Rate-limit / per-source budget bookkeeping.
//!
//! Mirrors Python's `.collector_state.json` shape so that — when we
//! eventually want the two pipelines to share a budget — they can
//! read each other's call counters. For now the Rust fetcher keeps
//! its own state file under `rust-fetcher/data/.fetcher_state.json`.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct State {
    /// e.g. "2026-06-19"
    pub date: String,
    /// e.g. "2026-06"
    pub month: String,
    /// Per-source daily call counter.
    #[serde(default)]
    pub calls: HashMap<String, u32>,
    /// Per-source monthly call counter.
    #[serde(default)]
    pub monthly_calls: HashMap<String, u32>,
}

impl State {
    /// Load — missing file yields a default (zeroed) state.
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let bytes = std::fs::read(path)?;
        let s = serde_json::from_slice(&bytes).unwrap_or_default();
        Ok(s)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, serde_json::to_vec_pretty(self)?)?;
        Ok(())
    }

    /// Reset the per-day counters if the calendar day rolled over.
    pub fn roll_day(&mut self, today: &str) {
        if self.date != today {
            self.date = today.to_string();
            self.calls.clear();
        }
    }

    /// Reset the monthly counters if the calendar month rolled over.
    pub fn roll_month(&mut self, this_month: &str) {
        if self.month != this_month {
            self.month = this_month.to_string();
            self.monthly_calls.clear();
        }
    }

    pub fn record_call(&mut self, source: &str) {
        *self.calls.entry(source.to_string()).or_insert(0) += 1;
        *self.monthly_calls.entry(source.to_string()).or_insert(0) += 1;
    }

    pub fn daily(&self, source: &str) -> u32 {
        self.calls.get(source).copied().unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_and_query() {
        let mut s = State::default();
        s.record_call("alphavantage");
        s.record_call("alphavantage");
        s.record_call("yfinance");
        assert_eq!(s.daily("alphavantage"), 2);
        assert_eq!(s.daily("yfinance"), 1);
        assert_eq!(s.daily("never_called"), 0);
    }

    #[test]
    fn day_rollover_clears_daily_only() {
        let mut s = State::default();
        s.date  = "2026-06-18".into();
        s.month = "2026-06".into();
        s.record_call("av");
        s.roll_day("2026-06-19");
        assert_eq!(s.daily("av"), 0, "daily counter resets on day change");
        assert_eq!(
            s.monthly_calls.get("av").copied().unwrap_or(0), 1,
            "monthly counter survives a day rollover",
        );
    }

    #[test]
    fn round_trip_through_disk() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let mut s = State::default();
        s.date = "2026-06-19".into();
        s.record_call("av");
        s.save(tmp.path()).unwrap();
        let back = State::load(tmp.path()).unwrap();
        assert_eq!(back.date, "2026-06-19");
        assert_eq!(back.daily("av"), 1);
    }
}

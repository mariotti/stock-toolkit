//! SQLite storage. Schema is byte-compatible with Python's
//! `prices` table (see `pyApi/SCHEMA.md` — the column-level
//! compatibility commitment is what makes a future cross-language
//! merge mechanical).
//!
//! The `UNIQUE(symbol, source, timestamp)` constraint handles
//! deduplication: re-running a fetch is safe, duplicate inserts
//! turn into no-ops via `INSERT OR IGNORE`.

use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use std::path::Path;

/// One bar of OHLCV data. Field naming matches Python's
/// `stock_toolkit.collector` writer exactly so the DDL below is
/// the cross-language contract.
#[derive(Debug, Clone, PartialEq)]
pub struct PriceRow {
    pub fetched_at: String,         // ISO-8601 with timezone
    pub symbol:     String,
    pub source:     String,
    pub timestamp:  String,         // ISO-8601 with timezone
    pub interval:   String,         // e.g. "1d"
    pub open:       Option<f64>,
    pub high:       Option<f64>,
    pub low:        Option<f64>,
    pub close:      Option<f64>,
    pub volume:     Option<i64>,
    pub vwap:       Option<f64>,
    pub change_pct: Option<f64>,
    pub extra:      Option<String>, // JSON blob, source-specific
}

/// SQLite handle. Opens in WAL mode so a concurrent Python
/// reader doesn't block on the Rust writer.
pub struct Db {
    conn: Connection,
}

impl Db {
    /// Open or create the database at `path`. Idempotent — running
    /// twice on the same file just re-asserts the schema.
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating {}", parent.display()))?;
        }
        let conn = Connection::open(path)
            .with_context(|| format!("opening {}", path.display()))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn })
    }

    /// Insert a batch of rows in one transaction. Duplicates
    /// (same `(symbol, source, timestamp)`) are silently ignored.
    /// Returns the count of rows actually inserted (excluding
    /// dedup misses).
    pub fn insert_batch(&mut self, rows: &[PriceRow]) -> Result<usize> {
        let tx = self.conn.transaction()?;
        let mut inserted = 0usize;
        {
            let mut stmt = tx.prepare(
                "INSERT OR IGNORE INTO prices \
                 (fetched_at, symbol, source, timestamp, interval, \
                  open, high, low, close, volume, vwap, change_pct, extra) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            )?;
            for r in rows {
                let n = stmt.execute(params![
                    r.fetched_at, r.symbol, r.source, r.timestamp, r.interval,
                    r.open, r.high, r.low, r.close, r.volume, r.vwap,
                    r.change_pct, r.extra,
                ])?;
                inserted += n;
            }
        }
        tx.commit()?;
        Ok(inserted)
    }

    /// Total row count — handy for tests and the `--summary` CLI mode.
    pub fn row_count(&self) -> Result<i64> {
        Ok(self.conn.query_row("SELECT COUNT(*) FROM prices", [], |r| r.get(0))?)
    }

    /// Per-(symbol, source) counts. Sorted by count desc then alpha.
    pub fn per_source_counts(&self) -> Result<Vec<(String, String, i64)>> {
        let mut stmt = self.conn.prepare(
            "SELECT symbol, source, COUNT(*) FROM prices \
             GROUP BY symbol, source ORDER BY COUNT(*) DESC, symbol, source",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }
}

/// DDL kept inline so the Rust binary is self-sufficient — no
/// reference to a migration file required to bootstrap a fresh DB.
///
/// **This is the cross-language schema contract.** Any column
/// change here must mirror in `pyApi/stock_toolkit/collector/db.py`
/// AND bump the major version of both crates. See `pyApi/SCHEMA.md`
/// for the column-level rules.
pub const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS prices (
    fetched_at  TEXT,
    symbol      TEXT,
    source      TEXT,
    timestamp   TEXT,
    interval    TEXT,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    vwap        REAL,
    change_pct  REAL,
    extra       TEXT,
    UNIQUE(symbol, source, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol    ON prices(symbol);
CREATE INDEX IF NOT EXISTS idx_prices_timestamp ON prices(timestamp);
";

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(symbol: &str, ts: &str, close: f64) -> PriceRow {
        PriceRow {
            fetched_at: "2026-01-01T00:00:00+00:00".to_string(),
            symbol:     symbol.to_string(),
            source:     "alphavantage".to_string(),
            timestamp:  ts.to_string(),
            interval:   "1d".to_string(),
            open:       Some(close - 0.5),
            high:       Some(close + 0.5),
            low:        Some(close - 1.0),
            close:      Some(close),
            volume:     Some(1_000_000),
            vwap:       None,
            change_pct: None,
            extra:      None,
        }
    }

    #[test]
    fn schema_creates_and_inserts() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let mut db = Db::open(tmp.path()).unwrap();
        let n = db.insert_batch(&[
            sample("AAPL", "2026-01-02T00:00:00+00:00", 200.0),
            sample("AAPL", "2026-01-03T00:00:00+00:00", 201.0),
        ]).unwrap();
        assert_eq!(n, 2);
        assert_eq!(db.row_count().unwrap(), 2);
    }

    #[test]
    fn duplicates_are_ignored() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let mut db = Db::open(tmp.path()).unwrap();
        let row = sample("AAPL", "2026-01-02T00:00:00+00:00", 200.0);
        let first  = db.insert_batch(&[row.clone()]).unwrap();
        let second = db.insert_batch(&[row.clone()]).unwrap();
        assert_eq!(first,  1);
        assert_eq!(second, 0, "duplicate insert must be a no-op");
        assert_eq!(db.row_count().unwrap(), 1);
    }

    #[test]
    fn per_source_counts_groups_correctly() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let mut db = Db::open(tmp.path()).unwrap();
        db.insert_batch(&[
            sample("AAPL", "2026-01-02T00:00:00+00:00", 200.0),
            sample("AAPL", "2026-01-03T00:00:00+00:00", 201.0),
            sample("MSFT", "2026-01-02T00:00:00+00:00", 400.0),
        ]).unwrap();
        let counts = db.per_source_counts().unwrap();
        assert_eq!(counts.len(), 2);
        assert_eq!(counts[0], ("AAPL".to_string(), "alphavantage".to_string(), 2));
        assert_eq!(counts[1], ("MSFT".to_string(), "alphavantage".to_string(), 1));
    }

    #[test]
    fn idempotent_open_reasserts_schema() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        Db::open(tmp.path()).unwrap();
        // Re-open must not error ("table prices already exists") —
        // schema uses IF NOT EXISTS.
        Db::open(tmp.path()).unwrap();
    }
}

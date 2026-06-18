//! `stock-fetcher` — CLI entry point.
//!
//! Drives the source modules concurrently against the watchlist
//! and persists into `rust-fetcher/data/stock_data.db` (or the
//! path passed via `--db`). Config + state are loaded from the
//! same `config.env` Python uses.
//!
//! ```
//! stock-fetcher --sources alphavantage
//! stock-fetcher --sources alphavantage --symbols AAPL,MSFT
//! stock-fetcher --summary
//! ```

use anyhow::{anyhow, Context, Result};
use clap::Parser;
use std::path::PathBuf;
use std::sync::Arc;
use stock_fetcher::{Config, Db, Source};
use stock_fetcher::sources::alphavantage::AlphaVantage;
use tokio::sync::Semaphore;
use tracing_subscriber::EnvFilter;

#[derive(Parser, Debug)]
#[command(version, about = "Concurrent stock data fetcher (Rust)")]
struct Cli {
    /// Comma-separated list of sources to run. Currently supported:
    /// `alphavantage`. (More to follow.)
    #[arg(long, value_delimiter = ',', default_value = "alphavantage")]
    sources: Vec<String>,

    /// Comma-separated override of the watchlist. Empty → uses
    /// `SYMBOLS` from `config.env`.
    #[arg(long, value_delimiter = ',')]
    symbols: Vec<String>,

    /// Path to `config.env`. Defaults to `../pyApi/config.env`
    /// (the canonical location for a checkout-style layout).
    #[arg(long)]
    config: Option<PathBuf>,

    /// Path to the output SQLite database. Defaults to
    /// `./data/stock_data.db` next to the binary's working dir.
    #[arg(long)]
    db: Option<PathBuf>,

    /// Max number of concurrent symbol fetches per source. Tune
    /// down if you start hitting per-source rate limits.
    #[arg(long, default_value_t = 4)]
    concurrency: usize,

    /// Print row counts and exit. No fetching.
    #[arg(long)]
    summary: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let cli = Cli::parse();

    let config_path = cli.config.unwrap_or_else(|| {
        // Default looks one level up at the Python config — the
        // typical "developer runs from rust-fetcher/" location.
        PathBuf::from("../pyApi/config.env")
    });
    let db_path = cli.db.unwrap_or_else(|| PathBuf::from("data/stock_data.db"));

    let cfg = Config::load(&config_path)
        .with_context(|| format!("loading {}", config_path.display()))?;

    let symbols: Vec<String> = if cli.symbols.is_empty() {
        cfg.csv("SYMBOLS")
    } else {
        cli.symbols
    };

    // --summary mode is a read-only inspection: open the DB and
    // dump counts. Fast path, no fetcher constructed.
    if cli.summary {
        let db = Db::open(&db_path)?;
        let total = db.row_count()?;
        let per   = db.per_source_counts()?;
        println!("Total rows: {total}");
        println!("Per (symbol, source):");
        for (sym, src, n) in per {
            println!("  {sym:<10} {src:<14} {n}");
        }
        return Ok(());
    }

    if symbols.is_empty() {
        return Err(anyhow!(
            "no symbols configured: pass --symbols or set SYMBOLS in {}",
            config_path.display(),
        ));
    }

    tracing::info!(
        sources = ?cli.sources, symbols = ?symbols,
        db = %db_path.display(),
        "starting fetch",
    );

    let mut db = Db::open(&db_path)?;

    for source_name in &cli.sources {
        let source: Arc<dyn Source> = match source_name.as_str() {
            "alphavantage" => {
                let key = cfg.get("ALPHAVANTAGE_KEY")
                    .ok_or_else(|| anyhow!(
                        "ALPHAVANTAGE_KEY not set in {}",
                        config_path.display(),
                    ))?
                    .to_string();
                Arc::new(AlphaVantage::new(key))
            }
            other => {
                return Err(anyhow!(
                    "unknown source '{other}'. Currently supported: \
                     alphavantage",
                ));
            }
        };

        // Per-source semaphore caps concurrent in-flight requests.
        // Free-tier AV is 25/day rather than per-second, but other
        // sources (Finnhub 60/min, Polygon 5/min) will need a real
        // rate limiter — the semaphore is the seam where that goes.
        let sem = Arc::new(Semaphore::new(cli.concurrency));
        let mut handles = Vec::with_capacity(symbols.len());

        for sym in &symbols {
            let sem    = sem.clone();
            let source = source.clone();
            let sym    = sym.clone();
            handles.push(tokio::spawn(async move {
                let _permit = sem.acquire().await.expect("semaphore");
                match source.fetch_daily(&sym).await {
                    Ok(rows) => Ok((sym, rows)),
                    Err(e)   => Err((sym, e)),
                }
            }));
        }

        let mut inserted_total = 0usize;
        let mut fetched_total  = 0usize;
        for h in handles {
            match h.await? {
                Ok((sym, rows)) => {
                    fetched_total += rows.len();
                    let n = db.insert_batch(&rows)?;
                    inserted_total += n;
                    tracing::info!(
                        source = %source_name, symbol = %sym,
                        fetched = rows.len(), inserted = n,
                        "ok",
                    );
                }
                Err((sym, e)) => {
                    tracing::warn!(
                        source = %source_name, symbol = %sym,
                        error = %e,
                        "fetch failed",
                    );
                }
            }
        }

        tracing::info!(
            source = %source_name,
            symbols = symbols.len(),
            fetched = fetched_total,
            inserted = inserted_total,
            duplicates_skipped = fetched_total - inserted_total,
            "source done",
        );
    }

    Ok(())
}

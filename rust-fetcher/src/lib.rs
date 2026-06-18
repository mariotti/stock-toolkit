//! # stock_fetcher
//!
//! Concurrent stock data fetcher — Rust companion to the existing
//! Python `stock_toolkit.collector`. Reads the same `config.env`
//! the Python side uses (single source of truth for API keys and
//! symbols), writes to its own SQLite database whose schema is
//! deliberately byte-compatible with Python's `prices` table — so
//! a future cross-language merge is mechanical, not a migration.
//!
//! ## Layering
//!
//! ```text
//!   config.env  ──►  Config           single source of truth
//!                       │
//!                       ▼
//!                    Fetcher          per-source async loop
//!                       │             rate-limit aware
//!                       ▼
//!                    PriceRow         normalised record
//!                       │
//!                       ▼
//!                    Db.insert        INSERT OR IGNORE dedup
//! ```
//!
//! ## What this module exports
//!
//! Everything you'd need to drive the fetcher from a binary or an
//! integration test. The binary (`src/main.rs`) is a thin CLI on
//! top of these primitives — no business logic lives there.

pub mod config;
pub mod db;
pub mod sources;
pub mod state;

pub use crate::config::Config;
pub use crate::db::{Db, PriceRow};
pub use crate::sources::Source;

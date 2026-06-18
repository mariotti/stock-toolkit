//! `config.env` parser. Byte-compatible with the Python reader in
//! `stock_toolkit.common.load_config` — same comment rules, same
//! inline-comment stripping, same quote handling. That symmetry is
//! the whole point: keys and watchlist live in one file, both
//! pipelines read it.
//!
//! See `pyApi/stock_toolkit/common.py::load_config` for the
//! reference implementation. Any change in semantics here MUST be
//! mirrored there (and the contract tested in
//! `tests/config_parity_test.rs`).

use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;

/// Parsed config.env as `KEY → value`. Missing keys return `None`
/// on `get`; the orchestrator decides whether the absence is fatal.
#[derive(Debug, Clone, Default)]
pub struct Config {
    map: HashMap<String, String>,
}

impl Config {
    /// Parse `config.env` from disk. Missing file → empty config (no
    /// error). Matches Python's `load_config` behaviour.
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("reading {}", path.display()))?;
        Ok(Self::parse(&text))
    }

    /// Parse from a string. Public for ease of testing.
    pub fn parse(text: &str) -> Self {
        let mut map = HashMap::new();
        for raw in text.lines() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some(eq) = line.find('=') else { continue };
            let key = line[..eq].trim().to_string();
            let mut val = line[eq + 1..].trim().to_string();

            // strip inline comment — matches Python's logic
            if val.starts_with('#') {
                val.clear();
            } else if let Some(idx) = val.find(" #") {
                val = val[..idx].trim().to_string();
            }
            // strip matching quotes
            let bytes = val.as_bytes();
            if bytes.len() >= 2
                && ((bytes[0] == b'"'  && bytes[bytes.len() - 1] == b'"')
                 || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''))
            {
                val = val[1..val.len() - 1].to_string();
            }
            map.insert(key, val);
        }
        Self { map }
    }

    /// Look up `key`. Returns `None` if the key is missing OR its
    /// value is the empty string (matches "blank means unset"
    /// convention used everywhere in the codebase).
    pub fn get(&self, key: &str) -> Option<&str> {
        self.map
            .get(key)
            .map(|v| v.as_str())
            .filter(|s| !s.is_empty())
    }

    /// Comma-separated value as a vec. Empty/missing → `vec![]`.
    /// Whitespace around each item is trimmed.
    pub fn csv(&self, key: &str) -> Vec<String> {
        self.get(key)
            .map(|s| {
                s.split(',')
                    .map(|p| p.trim().to_string())
                    .filter(|p| !p.is_empty())
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_keys() {
        let cfg = Config::parse(
            "SYMBOLS=AAPL,MSFT\nALPHAVANTAGE_KEY=secret\n",
        );
        assert_eq!(cfg.get("SYMBOLS"), Some("AAPL,MSFT"));
        assert_eq!(cfg.get("ALPHAVANTAGE_KEY"), Some("secret"));
    }

    #[test]
    fn empty_value_treated_as_missing() {
        let cfg = Config::parse("OPTIONAL=\n");
        assert_eq!(cfg.get("OPTIONAL"), None);
    }

    #[test]
    fn inline_comment_stripped() {
        let cfg = Config::parse("KEY=value # inline\n");
        assert_eq!(cfg.get("KEY"), Some("value"));
    }

    #[test]
    fn quoted_value_unwrapped() {
        let cfg = Config::parse("Q=\"with spaces\"\nS='single quoted'\n");
        assert_eq!(cfg.get("Q"), Some("with spaces"));
        assert_eq!(cfg.get("S"), Some("single quoted"));
    }

    #[test]
    fn comment_only_line_skipped() {
        let cfg = Config::parse("# this whole line\nKEY=v\n");
        assert_eq!(cfg.get("KEY"), Some("v"));
    }

    #[test]
    fn csv_helper_handles_blanks_and_whitespace() {
        let cfg = Config::parse("SYMBOLS=AAPL, MSFT , , GOOGL\n");
        assert_eq!(
            cfg.csv("SYMBOLS"),
            vec!["AAPL".to_string(), "MSFT".to_string(), "GOOGL".to_string()],
        );
        assert_eq!(cfg.csv("MISSING"), Vec::<String>::new());
    }

    #[test]
    fn missing_file_yields_empty_config() {
        let cfg = Config::load(Path::new("/tmp/does-not-exist-zzz.env")).unwrap();
        assert!(cfg.get("ANYTHING").is_none());
    }
}

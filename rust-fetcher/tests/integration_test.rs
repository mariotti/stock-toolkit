//! End-to-end test: spin up a wiremock HTTP server, point an
//! `AlphaVantage` source at it, drive `fetch_daily` → parse →
//! persist → assert the DB has what we expect.
//!
//! Mirrors the "offline journey test" pattern from the Python side
//! — exercises the full pipeline without any real network.

use serde_json::json;
use stock_fetcher::sources::alphavantage::AlphaVantage;
use stock_fetcher::{Db, Source};
use wiremock::matchers::{method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn fetch_parse_persist_round_trip() {
    let server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/query"))
        .and(query_param("function", "TIME_SERIES_DAILY"))
        .and(query_param("symbol",   "AAPL"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "Meta Data": {},
            "Time Series (Daily)": {
                "2026-06-18": {
                    "1. open": "200.0", "2. high": "205.0",
                    "3. low":  "199.0", "4. close": "203.5",
                    "5. volume": "12345678",
                },
                "2026-06-17": {
                    "1. open": "198.0", "2. high": "201.0",
                    "3. low":  "197.0", "4. close": "200.0",
                    "5. volume": "10000000",
                },
            },
        })))
        .mount(&server)
        .await;

    let src = AlphaVantage::new("fake-key").with_base(server.uri());
    let rows = src.fetch_daily("AAPL").await.expect("fetch");
    assert_eq!(rows.len(), 2);

    let tmp = tempfile::NamedTempFile::new().unwrap();
    let mut db = Db::open(tmp.path()).expect("open db");
    let n = db.insert_batch(&rows).expect("insert");
    assert_eq!(n, 2);
    assert_eq!(db.row_count().unwrap(), 2);

    // Second pass: same rows → dedup, zero new inserts.
    let n2 = db.insert_batch(&rows).expect("insert again");
    assert_eq!(n2, 0, "second pass must be a no-op");
    assert_eq!(db.row_count().unwrap(), 2);
}

#[tokio::test]
async fn throttle_response_yields_no_rows() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/query"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "Note": "Thank you for using Alpha Vantage..."
        })))
        .mount(&server)
        .await;

    let src = AlphaVantage::new("fake").with_base(server.uri());
    let rows = src.fetch_daily("AAPL").await.unwrap();
    assert!(rows.is_empty(), "throttle reply must return no rows, not an error");
}

#[tokio::test]
async fn non_2xx_yields_no_rows() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/query"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server)
        .await;

    let src = AlphaVantage::new("fake").with_base(server.uri());
    let rows = src.fetch_daily("AAPL").await.unwrap();
    assert!(rows.is_empty());
}

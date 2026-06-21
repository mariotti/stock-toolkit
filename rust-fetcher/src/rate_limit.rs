//! Token-bucket rate limiter.
//!
//! Each per-source quota gets a `RateLimit`. Callers `acquire().await`
//! before doing the HTTP round-trip; the call blocks until a token is
//! available, then proceeds. Tokens refill at a steady rate that
//! matches the source's documented per-minute / per-day cap.
//!
//! Why a token bucket rather than a fixed-spacing throttle:
//!
//! - **Bursts are fine.** Finnhub allows 60 calls/minute, so the
//!   first 60 should go out essentially at once if we're idle. A
//!   pure `tokio::time::interval(1s)` would force-pace one call per
//!   second even when we're well under quota.
//! - **Composes with the per-source semaphore** (concurrency cap).
//!   The semaphore caps *in-flight* requests; this caps *requests
//!   per unit time*. Both apply.
//!
//! The implementation is intentionally minimal: refill rate +
//! capacity, no priority queue, no jitter. Good enough for the seven
//! sources we care about; expandable when a real producer-grade
//! limiter is needed.

use std::time::Duration;
use tokio::sync::Mutex;
use tokio::time::Instant;

/// A token bucket configured by `capacity` (max burst) and
/// `refill_period` (how long it takes to refill one token).
///
/// Construct via the named helpers — `per_minute`, `per_day` —
/// rather than the raw `new`; they document the source's
/// documented limit in the call site.
pub struct RateLimit {
    state: Mutex<State>,
}

struct State {
    /// Available tokens, in units of 1/scale.
    /// Stored at higher resolution than integer tokens so we can
    /// refill smoothly between whole-token boundaries.
    tokens_scaled: i64,
    /// Max tokens × scale.
    capacity_scaled: i64,
    /// 1 token / refill_period; expressed as scaled units per second.
    refill_per_sec_scaled: f64,
    /// When we last accounted for refill.
    last_refill: Instant,
}

const SCALE: i64 = 1_000_000;

impl RateLimit {
    /// `n` tokens per minute, with a `n`-sized burst capacity.
    /// Matches the natural source-doc shape ("Finnhub: 60 calls/min").
    pub fn per_minute(n: u32) -> Self {
        Self::new(n, Duration::from_secs(60) / n.max(1))
    }

    /// `n` tokens per second, with a `n`-sized burst capacity. Use
    /// this when the source enforces a strict per-second cap (e.g.
    /// Alpha Vantage free tier: 1 req/sec — burst of 1 is exactly
    /// what its limiter wants; anything bursty gets throttled).
    pub fn per_second(n: u32) -> Self {
        Self::new(n, Duration::from_secs(1) / n.max(1))
    }

    /// `n` tokens per day, evenly paced. Matches "Alpha Vantage:
    /// 25 calls/day". Bursting all 25 at once is allowed (capacity
    /// = n) but if you've spent them, you'll wait ~57 minutes for
    /// the next one to refill.
    pub fn per_day(n: u32) -> Self {
        Self::new(n, Duration::from_secs(86_400) / n.max(1))
    }

    /// Raw constructor. `capacity` is the burst size; `refill_period`
    /// is how long it takes to refill one token from empty.
    pub fn new(capacity: u32, refill_period: Duration) -> Self {
        let refill_per_sec = (1.0 / refill_period.as_secs_f64()).max(0.0);
        let capacity_scaled = (capacity as i64) * SCALE;
        Self {
            state: Mutex::new(State {
                tokens_scaled:        capacity_scaled,
                capacity_scaled,
                refill_per_sec_scaled: refill_per_sec * SCALE as f64,
                last_refill:           Instant::now(),
            }),
        }
    }

    /// Acquire one token, blocking until available. After this
    /// returns, the caller has been "billed" one unit and can proceed
    /// with the HTTP request.
    pub async fn acquire(&self) {
        loop {
            let wait = {
                let mut s = self.state.lock().await;
                s.refill();
                if s.tokens_scaled >= SCALE {
                    s.tokens_scaled -= SCALE;
                    return;
                }
                // Not enough — work out the sleep to the next token.
                let deficit = (SCALE - s.tokens_scaled) as f64;
                Duration::from_secs_f64(deficit / s.refill_per_sec_scaled)
            };
            // Sleep outside the lock so other tasks can be billed
            // while we wait.
            tokio::time::sleep(wait).await;
        }
    }

    /// Best-effort non-blocking acquire. Returns `true` if a token
    /// was available and consumed, `false` otherwise. Useful when a
    /// caller wants to choose between "rate-limited, do it later"
    /// and "rate-limited, skip this call entirely".
    pub async fn try_acquire(&self) -> bool {
        let mut s = self.state.lock().await;
        s.refill();
        if s.tokens_scaled >= SCALE {
            s.tokens_scaled -= SCALE;
            true
        } else {
            false
        }
    }
}

impl State {
    fn refill(&mut self) {
        let now = Instant::now();
        let elapsed = now.duration_since(self.last_refill).as_secs_f64();
        let add = (elapsed * self.refill_per_sec_scaled) as i64;
        if add > 0 {
            self.tokens_scaled = (self.tokens_scaled + add).min(self.capacity_scaled);
            self.last_refill = now;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn initial_burst_fills_to_capacity() {
        let rl = RateLimit::per_minute(5);
        // 5 calls go through immediately (full bucket).
        for _ in 0..5 {
            assert!(rl.try_acquire().await, "burst capacity should allow 5 immediate");
        }
        // 6th hits an empty bucket.
        assert!(!rl.try_acquire().await, "6th call must hit rate limit");
    }

    #[tokio::test]
    async fn refills_after_sleep() {
        let rl = RateLimit::new(2, Duration::from_millis(50));
        rl.try_acquire().await;
        rl.try_acquire().await;
        assert!(!rl.try_acquire().await);
        // After ~60 ms, one token should be back.
        tokio::time::sleep(Duration::from_millis(60)).await;
        assert!(rl.try_acquire().await, "should have refilled by now");
    }

    #[tokio::test]
    async fn acquire_blocks_until_token_available() {
        let rl = std::sync::Arc::new(RateLimit::new(1, Duration::from_millis(100)));
        rl.try_acquire().await;     // drain
        let started = Instant::now();
        rl.acquire().await;          // must wait ~100 ms
        let waited = started.elapsed();
        assert!(
            waited >= Duration::from_millis(80),
            "acquire() should have waited for the refill (got {waited:?})",
        );
    }

    #[tokio::test]
    async fn concurrent_callers_serialised_by_bucket() {
        // 5/sec rate, 10 callers — total wall time should be at least
        // (10 - 5) tokens × (1s / 5) = ~1.0 s.
        let rl = std::sync::Arc::new(RateLimit::new(5, Duration::from_millis(200)));
        let started = Instant::now();
        let mut handles = vec![];
        for _ in 0..10 {
            let rl = rl.clone();
            handles.push(tokio::spawn(async move {
                rl.acquire().await;
            }));
        }
        for h in handles { h.await.unwrap(); }
        let elapsed = started.elapsed();
        assert!(
            elapsed >= Duration::from_millis(900),
            "10 callers @ 5 burst + 5/sec should take ≥ 1 s (got {elapsed:?})",
        );
    }
}

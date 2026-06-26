//! Core engine (Rust) — orkestrasi hot-path:
//!   ingest(WS) -> normalize -> publish candle (PUB)
//!   recv intent (PULL) -> risk gate -> execution -> publish event (PUB)
mod config;
mod exec;
mod ingest;
mod ipc;
mod normalize;
mod risk;
mod types;

use std::env;

use tokio::sync::mpsc;
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;

use config::Config;
use exec::{now_ms, Executor};
use ipc::{EventPub, MarketPub, SignalPull};
use normalize::Normalizer;
use risk::RiskGate;
use types::OrderEvent;

fn start_equity() -> f64 {
    env::var("START_EQUITY")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1000.0)
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_env("LOG_LEVEL").unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let cfg = Config::from_env();
    info!(
        "core start mode={} symbols={:?} ipc(market={}, signal={}, event={})",
        cfg.mode, cfg.symbols, cfg.ipc.market_pub, cfg.ipc.signal_push, cfg.ipc.event_pub
    );
    if cfg.is_live() {
        warn!("MODE LIVE — order menggunakan UANG NYATA.");
    }

    // --- IPC sockets ---
    let mut market_pub = MarketPub::bind(&cfg.ipc.market_pub).await?;
    let mut event_pub = EventPub::bind(&cfg.ipc.event_pub).await?;
    let mut signal_pull = SignalPull::bind(&cfg.ipc.signal_push).await?;

    // --- Task A: ingest -> normalize -> publish candle ---
    let (tx, mut rx) = mpsc::channel(8192);
    let ingest_cfg = cfg.clone();
    tokio::spawn(async move {
        ingest::run(ingest_cfg, tx).await;
    });

    let mut normalizer = Normalizer::new(cfg.tick_buffer_cap, cfg.ohlcv_bars);
    tokio::spawn(async move {
        while let Some(tick) = rx.recv().await {
            if let Some(candle) = normalizer.push(tick) {
                market_pub.publish(&candle).await;
            }
        }
    });

    // --- Task B (main): recv intent -> risk -> exec -> event ---
    let mut gate = RiskGate::new(cfg.risk.clone());
    let executor = Executor::new(&cfg);
    let equity = start_equity();
    let mut open_notional = 0.0_f64;

    info!("menunggu intent dari layanan Python…");
    loop {
        let Some(intent) = signal_pull.recv().await else {
            continue;
        };

        let now = now_ms();
        if gate.breaker_tripped(equity, now) {
            warn!("{}: ditolak — circuit breaker harian aktif", intent.symbol);
            event_pub
                .publish(&OrderEvent {
                    symbol: intent.symbol.clone(),
                    kind: "reject".into(),
                    side: Some(intent.side),
                    qty: 0.0,
                    price: intent.price,
                    sl: 0.0,
                    tp: 0.0,
                    note: "circuit_breaker".into(),
                    ts: now,
                })
                .await;
            continue;
        }

        let dec = gate.evaluate(&intent, equity, open_notional);
        if !dec.ok {
            info!("{}: risk gate tolak ({})", intent.symbol, dec.reason);
            event_pub
                .publish(&OrderEvent {
                    symbol: intent.symbol.clone(),
                    kind: "reject".into(),
                    side: Some(intent.side),
                    qty: 0.0,
                    price: intent.price,
                    sl: dec.sl,
                    tp: dec.tp,
                    note: dec.reason.clone(),
                    ts: now,
                })
                .await;
            continue;
        }

        let ev = executor.open(&intent, &dec).await;
        if ev.kind == "open" {
            open_notional += dec.notional;
        }
        event_pub.publish(&ev).await;
    }
}

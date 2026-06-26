//! Layer 1 — WS ingestion: combined aggTrade stream Binance USDⓈ-M.
use futures_util::StreamExt;
use tokio::sync::mpsc;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tracing::{info, warn};

use crate::config::Config;
use crate::types::Tick;

fn build_url(cfg: &Config) -> String {
    let streams = cfg
        .symbols
        .iter()
        .map(|s| format!("{s}@aggTrade"))
        .collect::<Vec<_>>()
        .join("/");
    format!("{}?streams={}", cfg.ws_combined_base, streams)
}

fn parse_tick(text: &str) -> Option<Tick> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    let d = v.get("data").unwrap_or(&v);
    if d.get("e")?.as_str()? != "aggTrade" {
        return None;
    }
    Some(Tick {
        symbol: d.get("s")?.as_str()?.to_string(),
        price: d.get("p")?.as_str()?.parse().ok()?,
        qty: d.get("q")?.as_str()?.parse().ok()?,
        ts: d.get("T")?.as_i64()?,
    })
}

/// Loop koneksi dengan auto-reconnect; kirim tick ke channel.
pub async fn run(cfg: Config, tx: mpsc::Sender<Tick>) {
    let url = build_url(&cfg);
    loop {
        info!("WS connect: {url}");
        match connect_async(&url).await {
            Ok((mut ws, _)) => {
                info!("WS terhubung ({} simbol)", cfg.symbols.len());
                while let Some(msg) = ws.next().await {
                    match msg {
                        Ok(Message::Text(t)) => {
                            let s: &str = &t; // Deref: String / Utf8Bytes -> str
                            if let Some(tick) = parse_tick(s) {
                                if tx.send(tick).await.is_err() {
                                    return; // konsumen mati
                                }
                            }
                        }
                        Ok(Message::Ping(_)) | Ok(Message::Pong(_)) => {}
                        Ok(Message::Close(_)) | Err(_) => break,
                        _ => {}
                    }
                }
                warn!("WS terputus, reconnect…");
            }
            Err(e) => warn!("WS gagal connect: {e}"),
        }
        tokio::time::sleep(std::time::Duration::from_millis(cfg.ws_reconnect_ms)).await;
    }
}

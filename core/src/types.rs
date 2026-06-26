//! Tipe data lintas modul (juga payload IPC ke/dari layanan Python).
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Long,
    Short,
}

impl Side {
    /// Sisi order pembuka di Binance.
    pub fn open(&self) -> &'static str {
        match self {
            Side::Long => "BUY",
            Side::Short => "SELL",
        }
    }
    /// Sisi order penutup (reduceOnly).
    pub fn close(&self) -> &'static str {
        match self {
            Side::Long => "SELL",
            Side::Short => "BUY",
        }
    }
}

/// Tick mentah dari aggTrade.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tick {
    pub symbol: String,
    pub price: f64,
    pub qty: f64,
    pub ts: i64,
}

/// Candle OHLCV ternormalisasi (dipublikasikan ke Python).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candle {
    pub symbol: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub open_time: i64,
}

/// Sinyal/intent dari Python (Layer 3-4) menuju core (risk + exec).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalIntent {
    pub symbol: String,
    pub side: Side,
    pub confidence: f64,
    pub price: f64,
    pub atr: f64,
}

/// Event hasil dari core (fill/close/reject) yang dipublikasikan balik ke Python.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderEvent {
    pub symbol: String,
    pub kind: String, // open | reject | close | error
    pub side: Option<Side>,
    pub qty: f64,
    pub price: f64,
    pub sl: f64,
    pub tp: f64,
    pub note: String,
    pub ts: i64,
}

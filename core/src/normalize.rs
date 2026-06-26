//! Layer 1 — data normalizer: ring buffer tick + agregasi OHLCV per timeframe.
use std::collections::{HashMap, VecDeque};

use crate::types::{Candle, Tick};

const BUCKET_MS: i64 = 60_000; // candle 1 menit (base); TF lebih besar dibentuk di Python

struct Building {
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    bucket: i64,
}

struct SymbolState {
    ticks: VecDeque<Tick>,
    candles: VecDeque<Candle>,
    last_price: f64,
    building: Option<Building>,
}

pub struct Normalizer {
    tick_cap: usize,
    candle_cap: usize,
    state: HashMap<String, SymbolState>,
}

impl Normalizer {
    pub fn new(tick_cap: usize, candle_cap: usize) -> Self {
        Self {
            tick_cap,
            candle_cap,
            state: HashMap::new(),
        }
    }

    pub fn last_price(&self, symbol: &str) -> Option<f64> {
        self.state.get(symbol).map(|s| s.last_price)
    }

    /// Masukkan tick; kembalikan Candle bila satu bucket baru saja tertutup.
    pub fn push(&mut self, tick: Tick) -> Option<Candle> {
        let cap_t = self.tick_cap;
        let cap_c = self.candle_cap;
        let s = self.state.entry(tick.symbol.clone()).or_insert_with(|| SymbolState {
            ticks: VecDeque::with_capacity(cap_t),
            candles: VecDeque::with_capacity(cap_c),
            last_price: tick.price,
            building: None,
        });

        s.last_price = tick.price;
        if s.ticks.len() == cap_t {
            s.ticks.pop_front();
        }
        s.ticks.push_back(tick.clone());

        let bucket = (tick.ts / BUCKET_MS) * BUCKET_MS;
        let mut closed: Option<Candle> = None;

        match &mut s.building {
            Some(b) if b.bucket == bucket => {
                b.high = b.high.max(tick.price);
                b.low = b.low.min(tick.price);
                b.close = tick.price;
                b.volume += tick.qty;
            }
            maybe => {
                if let Some(prev) = maybe.take() {
                    let candle = Candle {
                        symbol: tick.symbol.clone(),
                        open: prev.open,
                        high: prev.high,
                        low: prev.low,
                        close: prev.close,
                        volume: prev.volume,
                        open_time: prev.bucket,
                    };
                    if s.candles.len() == cap_c {
                        s.candles.pop_front();
                    }
                    s.candles.push_back(candle.clone());
                    closed = Some(candle);
                }
                *maybe = Some(Building {
                    open: tick.price,
                    high: tick.price,
                    low: tick.price,
                    close: tick.price,
                    volume: tick.qty,
                    bucket,
                });
            }
        }
        closed
    }
}

//! Konfigurasi core dari environment (.env di root repo).
use std::env;

#[derive(Debug, Clone)]
pub struct RiskParams {
    pub account_risk_pct: f64,
    pub leverage: u32,
    pub max_portfolio_exposure_pct: f64,
    pub daily_max_loss_pct: f64,
    pub daily_max_trades: u32,
    pub sl_atr_mult: f64,
    pub tp_atr_mult: f64,
}

#[derive(Debug, Clone)]
pub struct ExecParams {
    pub slippage_guard_pct: f64,
    pub retry_max: u32,
    pub retry_backoff_ms: u64,
    pub order_timeout_ms: u64,
}

#[derive(Debug, Clone)]
pub struct IpcConfig {
    pub market_pub: String,
    pub signal_push: String,
    pub event_pub: String,
}

#[derive(Debug, Clone)]
pub struct Config {
    pub mode: String, // dry | test | live
    pub symbols: Vec<String>,
    pub ws_combined_base: String,
    pub rest_base: String,
    pub api_key: String,
    pub api_secret: String,
    pub recv_window: i64,
    pub tick_buffer_cap: usize,
    pub ohlcv_bars: usize,
    pub ws_reconnect_ms: u64,
    pub risk: RiskParams,
    pub exec: ExecParams,
    pub ipc: IpcConfig,
}

fn get(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn getf(key: &str, default: f64) -> f64 {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn getu<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

impl Config {
    pub fn from_env() -> Self {
        let _ = dotenvy::dotenv();
        let mode = get("MODE", "dry").to_lowercase();

        let (ws_combined_base, rest_base, api_key, api_secret) = match mode.as_str() {
            "live" => (
                "wss://fstream.binance.com/stream".to_string(),
                get("BINANCE_LIVE_REST", "https://fapi.binance.com"),
                get("BINANCE_LIVE_KEY", ""),
                get("BINANCE_LIVE_SECRET", ""),
            ),
            "test" => (
                "wss://stream.binancefuture.com/stream".to_string(),
                get("BINANCE_TEST_REST", "https://testnet.binancefuture.com"),
                get("BINANCE_TEST_KEY", ""),
                get("BINANCE_TEST_SECRET", ""),
            ),
            // dry: tetap konsumsi data publik live, tanpa kredensial
            _ => (
                "wss://fstream.binance.com/stream".to_string(),
                get("BINANCE_LIVE_REST", "https://fapi.binance.com"),
                String::new(),
                String::new(),
            ),
        };

        let symbols = get("SYMBOLS", "btcusdc,ethusdc,bnbusdc,solusdc")
            .split(',')
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect();

        Config {
            mode,
            symbols,
            ws_combined_base,
            rest_base,
            api_key,
            api_secret,
            recv_window: getu("BINANCE_RECV_WINDOW", 5000),
            tick_buffer_cap: getu("TICK_BUFFER_CAP", 65536usize),
            ohlcv_bars: getu("OHLCV_BUFFER_BARS", 500usize),
            ws_reconnect_ms: getu("WS_RECONNECT_MS", 1000u64),
            risk: RiskParams {
                account_risk_pct: getf("RISK_ACCOUNT_RISK_PCT", 0.5),
                leverage: getu("RISK_LEVERAGE", 3u32),
                max_portfolio_exposure_pct: getf("RISK_MAX_PORTFOLIO_EXPOSURE_PCT", 30.0),
                daily_max_loss_pct: getf("RISK_DAILY_MAX_LOSS_PCT", 3.0),
                daily_max_trades: getu("RISK_DAILY_MAX_TRADES", 20u32),
                sl_atr_mult: getf("RISK_SL_ATR_MULT", 1.5),
                tp_atr_mult: getf("RISK_TP_ATR_MULT", 2.6),
            },
            exec: ExecParams {
                slippage_guard_pct: getf("EXEC_SLIPPAGE_GUARD_PCT", 0.15),
                retry_max: getu("EXEC_RETRY_MAX", 3u32),
                retry_backoff_ms: getu("EXEC_RETRY_BACKOFF_MS", 120u64),
                order_timeout_ms: getu("EXEC_ORDER_TIMEOUT_MS", 2000u64),
            },
            ipc: IpcConfig {
                market_pub: get("ZMQ_MARKET_PUB", "tcp://127.0.0.1:5556"),
                signal_push: get("ZMQ_SIGNAL_PUSH", "tcp://127.0.0.1:5557"),
                event_pub: get("ZMQ_EVENT_PUB", "tcp://127.0.0.1:5558"),
            },
        }
    }

    pub fn is_dry(&self) -> bool {
        self.mode == "dry"
    }
    pub fn is_live(&self) -> bool {
        self.mode == "live"
    }
}

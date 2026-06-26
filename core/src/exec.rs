//! Layer 6 — execution engine: order placement bertanda tangan + proteksi SL/TP.
use std::time::{SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use sha2::Sha256;
use tracing::{error, info};

use crate::config::{Config, ExecParams};
use crate::risk::RiskDecision;
use crate::types::{OrderEvent, SignalIntent};

type HmacSha256 = Hmac<Sha256>;

pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

pub struct Executor {
    http: reqwest::Client,
    rest_base: String,
    api_key: String,
    api_secret: String,
    recv_window: i64,
    dry: bool,
    p: ExecParams,
}

impl Executor {
    pub fn new(cfg: &Config) -> Self {
        let http = reqwest::Client::builder()
            .timeout(std::time::Duration::from_millis(cfg.exec.order_timeout_ms))
            .build()
            .expect("reqwest client");
        Self {
            http,
            rest_base: cfg.rest_base.clone(),
            api_key: cfg.api_key.clone(),
            api_secret: cfg.api_secret.clone(),
            recv_window: cfg.recv_window,
            dry: cfg.is_dry(),
            p: cfg.exec.clone(),
        }
    }

    fn sign(&self, query: &str) -> String {
        let mut mac =
            HmacSha256::new_from_slice(self.api_secret.as_bytes()).expect("hmac key");
        mac.update(query.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    /// Kirim signed POST ke /fapi/v1/order dengan retry+backoff.
    async fn signed_order(&self, params: &str) -> anyhow::Result<String> {
        let mut last_err = String::new();
        for attempt in 0..=self.p.retry_max {
            let ts = now_ms();
            let query = format!("{params}&recvWindow={}&timestamp={ts}", self.recv_window);
            let sig = self.sign(&query);
            let url = format!("{}/fapi/v1/order?{query}&signature={sig}", self.rest_base);

            let res = self
                .http
                .post(&url)
                .header("X-MBX-APIKEY", &self.api_key)
                .send()
                .await;

            match res {
                Ok(r) if r.status().is_success() => {
                    return Ok(r.text().await.unwrap_or_default());
                }
                Ok(r) => {
                    let code = r.status();
                    last_err = format!("HTTP {code}: {}", r.text().await.unwrap_or_default());
                    if code.as_u16() == 400 {
                        break; // parameter cacat — semua retry akan gagal sama
                    }
                }
                Err(e) => last_err = e.to_string(),
            }
            if attempt < self.p.retry_max {
                tokio::time::sleep(std::time::Duration::from_millis(
                    self.p.retry_backoff_ms * (attempt as u64 + 1),
                ))
                .await;
            }
        }
        Err(anyhow::anyhow!(last_err))
    }

    pub async fn open(&self, intent: &SignalIntent, dec: &RiskDecision) -> OrderEvent {
        let sym_uc = intent.symbol.to_uppercase();
        let base = OrderEvent {
            symbol: sym_uc.clone(),
            kind: "open".into(),
            side: Some(intent.side),
            qty: dec.qty,
            price: intent.price,
            sl: dec.sl,
            tp: dec.tp,
            note: String::new(),
            ts: now_ms(),
        };

        if self.dry {
            info!(
                "[DRY] OPEN {} {} qty={:.6} @~{} SL={} TP={}",
                intent.side.open(),
                sym_uc,
                dec.qty,
                intent.price,
                dec.sl,
                dec.tp
            );
            return OrderEvent { note: "dry".into(), ..base };
        }

        let entry = format!(
            "symbol={}&side={}&type=MARKET&quantity={:.6}",
            sym_uc,
            intent.side.open(),
            dec.qty
        );
        match self.signed_order(&entry).await {
            Ok(_) => {
                self.place_protection(&sym_uc, intent, dec).await;
                info!("OPEN {} {} qty={:.6}", intent.side.open(), sym_uc, dec.qty);
                base
            }
            Err(e) => {
                error!("open {sym_uc} gagal: {e}");
                OrderEvent {
                    kind: "error".into(),
                    note: e.to_string(),
                    ..base
                }
            }
        }
    }

    async fn place_protection(&self, sym_uc: &str, intent: &SignalIntent, dec: &RiskDecision) {
        let close = intent.side.close();
        let sl = format!(
            "symbol={sym_uc}&side={close}&type=STOP_MARKET&stopPrice={:.6}&closePosition=true",
            dec.sl
        );
        let tp = format!(
            "symbol={sym_uc}&side={close}&type=TAKE_PROFIT_MARKET&stopPrice={:.6}&closePosition=true",
            dec.tp
        );
        if let Err(e) = self.signed_order(&sl).await {
            error!("pasang SL {sym_uc} gagal: {e}");
        }
        if let Err(e) = self.signed_order(&tp).await {
            error!("pasang TP {sym_uc} gagal: {e}");
        }
    }
}

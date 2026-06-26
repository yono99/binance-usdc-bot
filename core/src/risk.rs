//! Layer 5 — risk gate: keputusan in-process (sub-ms), tanpa I/O.
use crate::config::RiskParams;
use crate::types::{Side, SignalIntent};

#[derive(Debug, Clone)]
pub struct RiskDecision {
    pub ok: bool,
    pub qty: f64,
    pub sl: f64,
    pub tp: f64,
    pub notional: f64,
    pub reason: String,
}

impl RiskDecision {
    fn reject(reason: &str) -> Self {
        Self {
            ok: false,
            qty: 0.0,
            sl: 0.0,
            tp: 0.0,
            notional: 0.0,
            reason: reason.to_string(),
        }
    }
}

/// Hari berjalan (epoch-day UTC) untuk reset circuit breaker.
fn epoch_day(now_ms: i64) -> i64 {
    now_ms / 86_400_000
}

pub struct RiskGate {
    p: RiskParams,
    day: i64,
    realized_pnl: f64,
    trades: u32,
    halted: bool,
}

impl RiskGate {
    pub fn new(p: RiskParams) -> Self {
        Self {
            p,
            day: 0,
            realized_pnl: 0.0,
            trades: 0,
            halted: false,
        }
    }

    fn roll(&mut self, now_ms: i64) {
        let d = epoch_day(now_ms);
        if d != self.day {
            self.day = d;
            self.realized_pnl = 0.0;
            self.trades = 0;
            self.halted = false;
        }
    }

    /// Circuit breaker harian. true = STOP membuka posisi.
    pub fn breaker_tripped(&mut self, equity: f64, now_ms: i64) -> bool {
        self.roll(now_ms);
        if self.halted {
            return true;
        }
        let max_loss = -(self.p.daily_max_loss_pct.abs() / 100.0) * equity;
        if self.realized_pnl <= max_loss {
            self.halted = true;
            return true;
        }
        self.trades >= self.p.daily_max_trades
    }

    /// Sizing berbasis jarak SL (risk-per-trade tetap), cek exposure cap.
    pub fn evaluate(&self, intent: &SignalIntent, equity: f64, open_notional: f64) -> RiskDecision {
        if intent.atr <= 0.0 || intent.price <= 0.0 {
            return RiskDecision::reject("ATR/price tidak valid");
        }

        let (sl, tp) = match intent.side {
            Side::Long => (
                intent.price - intent.atr * self.p.sl_atr_mult,
                intent.price + intent.atr * self.p.tp_atr_mult,
            ),
            Side::Short => (
                intent.price + intent.atr * self.p.sl_atr_mult,
                intent.price - intent.atr * self.p.tp_atr_mult,
            ),
        };

        let risk_per_unit = (intent.price - sl).abs();
        if risk_per_unit <= 0.0 {
            return RiskDecision::reject("jarak SL nol");
        }

        let risk_budget = (self.p.account_risk_pct / 100.0) * equity;
        let mut qty = risk_budget / risk_per_unit;
        let mut notional = qty * intent.price;

        let max_expo = (self.p.max_portfolio_exposure_pct / 100.0) * equity;
        if open_notional + notional > max_expo {
            let allowed = (max_expo - open_notional).max(0.0);
            if allowed < notional * 0.5 {
                return RiskDecision::reject("exposure cap");
            }
            qty = allowed / intent.price;
            notional = qty * intent.price;
        }

        if qty <= 0.0 {
            return RiskDecision::reject("qty nol");
        }

        RiskDecision {
            ok: true,
            qty,
            sl,
            tp,
            notional,
            reason: "ok".into(),
        }
    }

    pub fn record_close(&mut self, pnl: f64, now_ms: i64) {
        self.roll(now_ms);
        self.realized_pnl += pnl;
        self.trades += 1;
    }
}

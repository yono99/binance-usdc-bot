"""Backtester event-driven untuk mengukur EXPECTANCY strategi.

Prinsip:
- Pakai signal engine yang sama persis dengan live (`bot.signals.evaluate`)
  agar yang diukur = yang dijalankan.
- TANPA lookahead: sinyal dihitung dari bar yang sudah TERTUTUP (≤ i-1),
  entry di OPEN bar i berikutnya.
- Biaya nyata dimasukkan: fee taker per sisi + slippage. Tanpa ini hasil
  backtest menipu.
- Metrik utama dalam R-multiple (kelipatan risiko) — independen dari sizing,
  inilah ukuran edge yang jujur. Kurva equity hanya ilustrasi compounding.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .exchange import Exchange
from .logger import log
from .signals import evaluate


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    sl: float
    tp: float
    r: float          # R-multiple net (sudah termasuk fee+slippage)
    bars_held: int
    reason: str       # tp | sl | eod


def fetch_history(ex: Exchange, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    tf_ms = ex.client.parse_timeframe(timeframe) * 1000
    since = ex.client.milliseconds() - bars * tf_ms
    rows: list = []
    while len(rows) < bars:
        chunk = ex.client.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not chunk:
            break
        rows += chunk
        since = chunk[-1][0] + tf_ms
        if len(chunk) < 1000:
            break
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"]).drop_duplicates("time")
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.set_index("time")


class Backtester:
    def __init__(self, cfg: dict, fee_pct: float = 0.04, slippage_pct: float = 0.02,
                 maker: bool = False):
        self.cfg = cfg
        self.fee = fee_pct / 100.0
        self.slip = slippage_pct / 100.0
        self.sl_mult = cfg["risk"]["sl_atr_mult"]
        self.tp_mult = cfg["risk"]["tp_atr_mult"]
        # maker=True: entry hanya via limit post-only di harga sinyal (close bar terakhir);
        # terisi HANYA bila harga menyentuhnya → order yang "kabur" (sering yg menang) hilang.
        self.maker = maker

    def _warmup(self) -> int:
        s = self.cfg["signals"]
        return max(s["ema_slow"], s["atr_period"], s["adx_period"], 20) + 5

    def run_symbol(self, symbol: str, df: pd.DataFrame) -> list[Trade]:
        if len(df) < self._warmup() + 10:
            log.warning(f"{symbol}: data kurang ({len(df)} bar), skip")
            return []

        trades: list[Trade] = []
        pos: dict | None = None
        warm = self._warmup()
        n = len(df)

        for i in range(warm, n):
            row = df.iloc[i]

            if pos is not None:
                hit_sl = row["low"] <= pos["sl"] if pos["side"] == "long" else row["high"] >= pos["sl"]
                hit_tp = row["high"] >= pos["tp"] if pos["side"] == "long" else row["low"] <= pos["tp"]
                exit_price = reason = None
                if hit_sl and hit_tp:
                    exit_price, reason = pos["sl"], "sl"   # konservatif: anggap SL dulu
                elif hit_sl:
                    exit_price, reason = pos["sl"], "sl"
                elif hit_tp:
                    exit_price, reason = pos["tp"], "tp"
                if exit_price is not None:
                    trades.append(self._close(pos, exit_price, df.index[i], i, reason))
                    pos = None

            if pos is None:
                sig = evaluate(symbol, df.iloc[:i], self.cfg)  # hanya bar tertutup
                if sig.actionable and sig.atr > 0:
                    if self.maker:
                        pos = self._maybe_maker(symbol, sig, df, i)
                    else:
                        pos = self._open(symbol, sig, row, df.index[i], i)

        if pos is not None:  # tutup di harga terakhir
            last = df.iloc[-1]
            trades.append(self._close(pos, last["close"], df.index[-1], n - 1, "eod"))

        return trades

    def _open(self, symbol: str, sig, row, ts, idx) -> dict:
        # taker: masuk di OPEN bar berikutnya + slippage merugikan
        entry = row["open"] * (1 + self.slip) if sig.side == "long" else row["open"] * (1 - self.slip)
        return self._open_at(symbol, sig, entry, ts, idx)

    def _open_at(self, symbol: str, sig, entry: float, ts, idx) -> dict:
        """Bentuk posisi dengan harga entry EKSAK (mis. limit maker terisi di harga limit)."""
        if sig.side == "long":
            sl = entry - sig.atr * self.sl_mult
            tp = entry + sig.atr * self.tp_mult
        else:
            sl = entry + sig.atr * self.sl_mult
            tp = entry - sig.atr * self.tp_mult
        return {
            "symbol": symbol, "side": sig.side, "entry": entry, "sl": sl, "tp": tp,
            "risk_per_unit": abs(entry - sl), "entry_time": ts, "entry_idx": idx,
        }

    def _maybe_maker(self, symbol: str, sig, df: pd.DataFrame, i: int) -> dict | None:
        """Limit post-only di harga sinyal (close bar i-1). Terisi HANYA bila bar i
        menyentuhnya: long bila low ≤ limit, short bila high ≥ limit. Else None (order
        kabur — tak kebagian). Ini memodelkan adverse-selection maker secara jujur."""
        limit = float(df["close"].iloc[i - 1])
        if sig.side == "long":
            if float(df["low"].iloc[i]) <= limit:
                return self._open_at(symbol, sig, limit, df.index[i], i)
        else:
            if float(df["high"].iloc[i]) >= limit:
                return self._open_at(symbol, sig, limit, df.index[i], i)
        return None

    def _close(self, pos: dict, raw_exit: float, ts, idx, reason: str) -> Trade:
        # slippage pada exit (arah merugikan)
        if pos["side"] == "long":
            exit_fill = raw_exit * (1 - self.slip)
            move = exit_fill - pos["entry"]
        else:
            exit_fill = raw_exit * (1 + self.slip)
            move = pos["entry"] - exit_fill
        rpu = pos["risk_per_unit"] or 1e-9
        gross_r = move / rpu
        fee_r = (self.fee * (pos["entry"] + exit_fill)) / rpu  # round-trip fee dalam R
        return Trade(
            symbol=pos["symbol"], side=pos["side"], entry_time=pos["entry_time"], exit_time=ts,
            entry=pos["entry"], exit=exit_fill, sl=pos["sl"], tp=pos["tp"],
            r=gross_r - fee_r, bars_held=idx - pos["entry_idx"], reason=reason,
        )


def compute_metrics(trades: list[Trade], cfg: dict, start_equity: float) -> dict:
    if not trades:
        return {"trades": 0}

    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))

    # simulasi equity (compounding, urut waktu masuk) untuk drawdown & return
    risk_frac = cfg["risk"]["account_risk_pct"] / 100.0
    equity = start_equity
    peak = start_equity
    max_dd = 0.0
    curve = []
    for t in sorted(trades, key=lambda x: x.entry_time):
        equity *= (1 + risk_frac * t.r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        curve.append(equity)

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "expectancy_r": sum(rs) / len(rs),
        "avg_win_r": (gross_w / len(wins)) if wins else 0.0,
        "avg_loss_r": (-gross_l / len(losses)) if losses else 0.0,
        "profit_factor": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "total_r": sum(rs),
        "max_drawdown_pct": max_dd * 100,
        "final_equity": equity,
        "return_pct": (equity / start_equity - 1) * 100,
        "avg_bars_held": sum(t.bars_held for t in trades) / len(trades),
        "tp_rate": sum(1 for t in trades if t.reason == "tp") / len(trades) * 100,
    }

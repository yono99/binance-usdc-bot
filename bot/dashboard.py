"""Dashboard web monitoring (FastAPI) — baca jurnal forward-test, sajikan stats.

Terpisah dari bot: ForwardTester menulis logs/trades.jsonl, dashboard membacanya.
  GET /            -> halaman HTML (auto-refresh)
  GET /api/stats   -> JSON statistik berjalan
"""
from __future__ import annotations

import json
from pathlib import Path

from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .settings_store import PRESETS, RuntimeSettings, load_settings, save_settings

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "logs" / "trades.jsonl"
STATUS = ROOT / "logs" / "status.json"
CLOSE_REQ = ROOT / "logs" / "close_requests.json"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def compute_stats(path: Path | None = None, start_equity: float = 1000.0) -> dict:
    events = read_events(path or JOURNAL)
    opens = {}
    closes = []
    for e in events:
        ev = e.get("event")
        if ev == "forward_open":
            opens[e["symbol"]] = e
        elif ev == "forward_close":
            opens.pop(e["symbol"], None)
            closes.append(e)

    rs = [float(e.get("r", 0)) for e in closes]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    liquidations = sum(1 for e in closes if e.get("reason") == "liq")
    gross_w = sum(wins)
    gross_l = abs(sum(r for r in rs if r <= 0))

    equity_curve = [start_equity] + [float(e.get("equity", start_equity)) for e in closes]
    # indeks titik likuidasi pada equity_curve (close ke-i -> titik i+1)
    liq_points = [i + 1 for i, e in enumerate(closes) if e.get("reason") == "liq"]

    per: dict[str, list[float]] = {}
    for e in closes:
        per.setdefault(e["symbol"], []).append(float(e.get("r", 0)))
    per_symbol = [
        {"symbol": s, "trades": len(v),
         "win_rate": round(sum(1 for r in v if r > 0) / len(v) * 100, 1),
         "sum_r": round(sum(v), 3)}
        for s, v in sorted(per.items())
    ]

    recent = [
        {"ts": e.get("ts"), "symbol": e.get("symbol"), "reason": e.get("reason"),
         "r": round(float(e.get("r", 0)), 3), "equity": round(float(e.get("equity", 0)), 2)}
        for e in closes[-25:][::-1]
    ]

    open_positions = [
        {"symbol": s, "side": o.get("side"), "entry": o.get("entry"),
         "sl": o.get("sl"), "tp": o.get("tp")}
        for s, o in opens.items()
    ]

    return {
        "trades": n,
        "liquidations": liquidations,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "expectancy_r": round(sum(rs) / n, 4) if n else 0.0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else (None if gross_w == 0 else float("inf")),
        "total_r": round(sum(rs), 3),
        "equity": round(equity_curve[-1], 2),
        "return_pct": round((equity_curve[-1] / start_equity - 1) * 100, 2),
        "equity_curve": equity_curve,
        "liq_points": liq_points,
        "open_positions": open_positions,
        "per_symbol": per_symbol,
        "recent": recent,
    }


app = FastAPI(title="Bot Monitor")


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    return JSONResponse(compute_stats())


@app.get("/api/settings")
def api_get_settings() -> JSONResponse:
    s = load_settings()
    d = asdict(s)
    d["techniques"] = list(PRESETS)
    d["timeframe"] = s.timeframe()
    d["liq_pct"] = round(s.liquidation_frac() * 100, 3)
    return JSONResponse(d)


@app.get("/api/status")
def api_bot_status() -> JSONResponse:
    if not STATUS.exists():
        return JSONResponse({})
    try:
        return JSONResponse(json.loads(STATUS.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({})


_acct = {"ts": 0.0, "data": None}


@app.get("/api/account")
def api_account() -> JSONResponse:
    import os
    import time
    if _acct["data"] and time.time() - _acct["ts"] < 30:
        return JSONResponse(_acct["data"])
    from .config import load_settings
    s = load_settings()
    if s.mode == "live" and os.getenv("BINANCE_LIVE_KEY"):
        try:
            from .exchange import Exchange
            bal = Exchange(s).client.fetch_balance()
            total = bal.get("total", {})
            usdc = float(total.get("USDC") or total.get("USDT") or 0)
            data = {"mode": "live", "api_valid": True, "balance_usdc": round(usdc, 2),
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
        except Exception as e:  # boundary
            data = {"mode": "live", "api_valid": False, "error": str(e)[:140],
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    else:
        data = {"mode": s.mode, "api_valid": None, "paper": True,
                "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    _acct.update(ts=time.time(), data=data)
    return JSONResponse(data)


_ex_cache: dict = {"ex": None}
_ohlcv_cache: dict = {}


def _get_ex():
    if _ex_cache["ex"] is None:
        from .config import load_settings
        from .exchange import Exchange
        _ex_cache["ex"] = Exchange(load_settings())
    return _ex_cache["ex"]


@app.get("/api/ohlcv")
def api_ohlcv(symbol: str, tf: str = "15m", limit: int = 120) -> JSONResponse:
    import time
    ck = (symbol, tf, limit)
    c = _ohlcv_cache.get(ck)
    if c and time.time() - c[0] < 30:
        return JSONResponse(c[1])
    try:
        df = _get_ex().ohlcv(symbol, tf, limit=limit)
        bars = [{"x": int(i.timestamp() * 1000), "o": float(o), "h": float(h),
                 "l": float(low), "c": float(c)}
                for i, o, h, low, c in zip(df.index, df["open"], df["high"], df["low"], df["close"])]
        from . import indicators as ind
        from .config import load_settings
        sig = load_settings().raw["signals"]
        close = df["close"]
        rnd = lambda s: [round(float(x), 6) for x in s]
        data = {"symbol": symbol, "tf": tf, "bars": bars,
                "ema_fast": rnd(ind.ema(close, sig["ema_fast"])),
                "ema_mid": rnd(ind.ema(close, sig["ema_mid"])),
                "ema_slow": rnd(ind.ema(close, sig["ema_slow"])),
                "rsi": [round(float(x), 2) for x in ind.rsi(close, sig["rsi_period"])],
                "periods": {"fast": sig["ema_fast"], "mid": sig["ema_mid"],
                            "slow": sig["ema_slow"], "rsi": sig["rsi_period"]}}
        _ohlcv_cache[ck] = (time.time(), data)
        return JSONResponse(data)
    except Exception as e:  # boundary
        return JSONResponse({"symbol": symbol, "error": str(e)[:140], "bars": []})


@app.post("/api/validate-key")
def api_validate_key(payload: dict) -> JSONResponse:
    """Validasi key/secret (transien — TIDAK disimpan/di-log). Kosong = pakai .env live."""
    import os
    key = (payload.get("key") or os.getenv("BINANCE_LIVE_KEY", "")).strip()
    secret = (payload.get("secret") or os.getenv("BINANCE_LIVE_SECRET", "")).strip()
    if not key or not secret:
        return JSONResponse({"valid": False, "error": "key/secret kosong (isi form atau .env)"})
    try:
        import ccxt
        c = ccxt.binanceusdm({"apiKey": key, "secret": secret, "enableRateLimit": True,
                              "options": {"defaultType": "future"}})
        bal = c.fetch_balance()
        total = bal.get("total", {})
        usdc = float(total.get("USDC") or total.get("USDT") or 0)
        return JSONResponse({"valid": True, "balance_usdc": round(usdc, 2)})
    except Exception as e:  # boundary
        return JSONResponse({"valid": False, "error": str(e)[:160]})


@app.post("/api/close")
def api_close(payload: dict) -> JSONResponse:
    """Antrekan permintaan tutup posisi; engine memprosesnya siklus berikutnya."""
    sym = payload.get("symbol")
    if not sym:
        return JSONResponse({"ok": False, "error": "symbol kosong"})
    reqs = []
    if CLOSE_REQ.exists():
        try:
            reqs = json.loads(CLOSE_REQ.read_text(encoding="utf-8"))
        except Exception:
            reqs = []
    if sym not in reqs:
        reqs.append(sym)
    CLOSE_REQ.write_text(json.dumps(reqs), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": sym})


@app.post("/api/close-all")
def api_close_all() -> JSONResponse:
    CLOSE_REQ.write_text(json.dumps(["*"]), encoding="utf-8")
    return JSONResponse({"ok": True})


@app.post("/api/settings")
def api_set_settings(payload: dict) -> JSONResponse:
    known = set(RuntimeSettings().__dict__)
    if isinstance(payload.get("symbols"), str):
        payload["symbols"] = [x.strip() for x in payload["symbols"].split(",") if x.strip()]
    s = RuntimeSettings(**{k: v for k, v in payload.items() if k in known}).clamp()
    save_settings(s)
    d = asdict(s)
    d["timeframe"] = s.timeframe()
    d["liq_pct"] = round(s.liquidation_frac() * 100, 3)
    return JSONResponse(d)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


PAGE = """<!doctype html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot Monitor — Forward Test</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1"></script>
<style>
  :root{--bg:#0b1220;--card:#131c2e;--bd:#243049;--fg:#e2e8f0;--mut:#8aa0c0;
        --green:#22c55e;--red:#ef4444;--accent:#6366f1}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--bd);display:flex;
         justify-content:space-between;align-items:center}
  h1{font-size:18px;margin:0}.sub{color:var(--mut);font-size:12px}
  .wrap{padding:24px;max-width:1100px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
  .card .lbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  .card .val{font-size:26px;font-weight:700;margin-top:6px}
  .pos{color:var(--green)}.neg{color:var(--red)}
  .panel{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-top:18px}
  .panel h2{font-size:14px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--bd)}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-weight:600;font-size:12px}
  .empty{color:var(--mut);padding:18px;text-align:center}
  tr.liqrow td{background:rgba(239,68,68,.18);color:#fecaca;font-weight:700}
  tr.liqrow td:first-child{box-shadow:inset 3px 0 0 var(--red)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px}
  label{display:flex;flex-direction:column;gap:4px;color:var(--mut);font-size:12px}
  input,select{background:#0b1220;border:1px solid var(--bd);color:var(--fg);border-radius:8px;padding:8px;font-size:14px}
  button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-weight:600;cursor:pointer}
  button:hover{opacity:.9}
  .danger{background:rgba(239,68,68,.12);border:1px solid var(--red);color:#fca5a5;padding:10px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}
  .ok{background:rgba(34,197,94,.1);border:1px solid var(--green);color:#86efac;padding:10px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}
  .line{font-size:14px;line-height:1.9}.line b{color:var(--fg)}
  .btnsm{background:var(--red);padding:4px 10px;font-size:12px;border-radius:6px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px;animation:p 2s infinite}
  @keyframes p{50%{opacity:.3}}
</style></head>
<body>
<header><div><h1>Bot Monitor</h1><div class="sub">Forward-test (paper) · data live</div></div>
  <div class="sub"><span class="dot"></span><span id="upd">memuat…</span></div></header>
<div class="wrap">
  <div class="panel" id="ctl">
    <h2>Kontrol Bot (paper)</h2>
    <div id="warn"></div>
    <div class="grid">
      <label>Status<select id="enabled"><option value="false">OFF</option><option value="true">ON (buka posisi)</option></select></label>
      <label>Teknik<select id="technique"></select></label>
      <label>Pair (pisah koma)<input id="symbols" placeholder="BTC/USDC:USDC,ETH/USDC:USDC"></label>
      <label>Leverage (x)<input id="leverage" type="number" min="1" max="125"></label>
      <label>Bet / margin (USD)<input id="bet_usd" type="number" min="0.1" step="0.1"></label>
      <label>Saldo (USD)<input id="balance_usd" type="number" min="0" step="0.1"></label>
      <label>Target profit % (0=ATR)<input id="target_profit_pct" type="number" min="0" step="0.1"></label>
      <label>Timeframe (otomatis)<input id="tf" disabled></label>
    </div>
    <button id="save">Simpan pengaturan</button>
    <span id="saved" class="sub"></span>
  </div>
  <div class="panel"><h2>Akun / API</h2>
    <div id="acct" class="line"></div>
    <div class="grid" style="margin-top:10px">
      <label>API Key (validasi)<input id="vkey" placeholder="kosong = pakai .env live"></label>
      <label>API Secret<input id="vsecret" type="password" placeholder="kosong = pakai .env live"></label>
    </div>
    <button id="vbtn">Validasi API Key</button> <span id="vres" class="sub"></span>
  </div>
  <div class="panel"><h2>Status Bot</h2><div id="botstatus" class="line"></div></div>
  <div class="panel"><h2>Aktivitas per Pair — screening & sinyal
    <button class="btnsm" style="float:right" onclick="closeAll()">Close All</button></h2>
    <div id="pairs"></div></div>
  <div class="panel"><h2>Chart Harga per Pair</h2>
    <div style="margin-bottom:10px;display:flex;gap:8px">
      <select id="chartsym"></select>
      <select id="charttf">
        <option>5m</option><option selected>15m</option><option>1h</option><option>4h</option>
      </select>
    </div>
    <canvas id="px" height="90"></canvas>
    <div class="sub" id="emacap" style="margin-top:6px"></div>
    <canvas id="rsi" height="34" style="margin-top:10px"></canvas></div>
  <div class="cards" id="cards"></div>
  <div class="panel"><h2>Kurva Equity</h2><canvas id="eq" height="90"></canvas></div>
  <div class="panel"><h2>Posisi Terbuka</h2><div id="open"></div></div>
  <div class="panel"><h2>Per Simbol</h2><div id="sym"></div></div>
  <div class="panel"><h2>Trade Terakhir</h2><div id="recent"></div></div>
</div>
<script>
let chart;
const f=(n,d=2)=>Number(n).toFixed(d);
const cls=v=>v>0?'pos':(v<0?'neg':'');
function card(lbl,val,c=''){return `<div class="card"><div class="lbl">${lbl}</div><div class="val ${c}">${val}</div></div>`}
function table(cols,rows,rowCls){
  if(!rows.length)return '<div class="empty">Belum ada data — jalankan forwardtest.py</div>';
  const h='<tr>'+cols.map(c=>`<th>${c.t}</th>`).join('')+'</tr>';
  const b=rows.map(r=>`<tr class="${rowCls?rowCls(r):''}">`+cols.map(c=>`<td class="${c.cls?c.cls(r):''}">${c.f?c.f(r):r[c.k]}</td>`).join('')+'</tr>').join('');
  return `<table>${h}${b}</table>`
}
async function load(){
  const s=await (await fetch('/api/stats')).json();
  const pf=s.profit_factor==null?'—':(s.profit_factor>1e6?'∞':f(s.profit_factor));
  document.getElementById('cards').innerHTML=
    card('Trades',s.trades)+
    card('Liquidations',s.liquidations||0,(s.liquidations>0?'neg':''))+
    card('Win Rate',f(s.win_rate,1)+'%')+
    card('Expectancy R',(s.expectancy_r>0?'+':'')+f(s.expectancy_r,3),cls(s.expectancy_r))+
    card('Profit Factor',pf)+
    card('Equity',f(s.equity,2))+
    card('Return',(s.return_pct>0?'+':'')+f(s.return_pct,2)+'%',cls(s.return_pct));
  document.getElementById('open').innerHTML=table(
    [{t:'Simbol',k:'symbol'},{t:'Sisi',k:'side',cls:r=>r.side==='long'?'pos':'neg'},
     {t:'Entry',f:r=>f(r.entry,4)},{t:'SL',f:r=>f(r.sl,4)},{t:'TP',f:r=>f(r.tp,4)}],s.open_positions);
  document.getElementById('sym').innerHTML=table(
    [{t:'Simbol',k:'symbol'},{t:'Trades',k:'trades'},{t:'Win%',f:r=>f(r.win_rate,1)},
     {t:'Σ R',f:r=>(r.sum_r>0?'+':'')+f(r.sum_r,3),cls:r=>cls(r.sum_r)}],s.per_symbol);
  document.getElementById('recent').innerHTML=table(
    [{t:'Waktu',f:r=>(r.ts||'').slice(11,19)},{t:'Simbol',k:'symbol'},
     {t:'Alasan',f:r=>r.reason==='liq'?'⚠ LIKUIDASI':r.reason},
     {t:'R',f:r=>(r.r>0?'+':'')+f(r.r,3),cls:r=>cls(r.r)},{t:'Equity',f:r=>f(r.equity,2)}],
    s.recent, r=>r.reason==='liq'?'liqrow':'');
  const ctx=document.getElementById('eq');
  const data=s.equity_curve, labels=data.map((_,i)=>i);
  const liq=new Set(s.liq_points||[]);
  const radii=data.map((_,i)=>liq.has(i)?5:0);
  const colors=data.map((_,i)=>liq.has(i)?'#ef4444':'#6366f1');
  if(chart){
    chart.data.labels=labels;
    const d=chart.data.datasets[0];
    d.data=data;d.pointRadius=radii;d.pointBackgroundColor=colors;d.pointBorderColor=colors;
    chart.update();
  } else chart=new Chart(ctx,{type:'line',data:{labels,datasets:[{data,borderColor:'#6366f1',
    backgroundColor:'rgba(99,102,241,.15)',fill:true,tension:.25,pointRadius:radii,
    pointBackgroundColor:colors,pointBorderColor:colors,pointHoverRadius:6,borderWidth:2}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>
      (liq.has(c.dataIndex)?'⚠ LIKUIDASI · ':'')+'equity '+Number(c.parsed.y).toFixed(2)}}},
      scales:{x:{display:false},y:{grid:{color:'#243049'},ticks:{color:'#8aa0c0'}}}}});
  document.getElementById('upd').textContent='diperbarui '+new Date().toLocaleTimeString();
}
function riskWarn(lev, liq){
  const w=document.getElementById('warn');
  if(lev>=50) w.innerHTML=`<div class="danger">⚠ Leverage ${lev}x: gerakan melawan ~${liq}% = LIKUIDASI (modal habis). SL berbasis ATR biasanya lebih lebar, jadi posisi kena likuidasi lebih dulu. Ini judi, bukan trading. Backtest strategi ini masih impas.</div>`;
  else if(lev>=20) w.innerHTML=`<div class="danger">⚠ Leverage ${lev}x berisiko tinggi: likuidasi pada gerakan ~${liq}%.</div>`;
  else w.innerHTML='';
}
async function loadSettings(){
  const s=await (await fetch('/api/settings')).json();
  const sel=document.getElementById('technique');
  sel.innerHTML=s.techniques.map(t=>`<option value="${t}">${t}</option>`).join('');
  document.getElementById('enabled').value=String(s.enabled);
  sel.value=s.technique;
  document.getElementById('symbols').value=(s.symbols||[]).join(',');
  document.getElementById('leverage').value=s.leverage;
  document.getElementById('bet_usd').value=s.bet_usd;
  document.getElementById('balance_usd').value=s.balance_usd;
  document.getElementById('target_profit_pct').value=s.target_profit_pct;
  document.getElementById('tf').value=s.timeframe;
  riskWarn(s.leverage, s.liq_pct);
  const csel=document.getElementById('chartsym');
  if(!csel.options.length && s.symbols && s.symbols.length)
    csel.innerHTML=s.symbols.map(x=>`<option>${x}</option>`).join('');
}
let pxChart, rsiChart;
async function loadChart(){
  const sym=document.getElementById('chartsym').value; if(!sym)return;
  const tf=document.getElementById('charttf').value||'15m';
  const d=await (await fetch('/api/ohlcv?symbol='+encodeURIComponent(sym)+'&tf='+tf+'&limit=120')).json();
  if(!d.bars||!d.bars.length)return;
  const ds=[{label:sym,type:'candlestick',data:d.bars,
             color:{up:'#22c55e',down:'#ef4444',unchanged:'#94a3b8'},
             borderColor:{up:'#22c55e',down:'#ef4444',unchanged:'#94a3b8'}}];
  const x0=d.bars[0].x, x1=d.bars[d.bars.length-1].x;
  const mkema=(arr,col)=>({type:'line',label:'',data:arr.map((y,i)=>({x:d.bars[i].x,y})),
    borderColor:col,borderWidth:1,pointRadius:0,tension:.2});
  if(d.ema_fast){
    ds.push(mkema(d.ema_fast,'#eab308'),mkema(d.ema_mid,'#3b82f6'),mkema(d.ema_slow,'#a855f7'));
    document.getElementById('emacap').innerHTML=
      `EMA${d.periods.fast} <span style="color:#eab308">━</span>  EMA${d.periods.mid} <span style="color:#3b82f6">━</span>  EMA${d.periods.slow} <span style="color:#a855f7">━</span>`;
  }
  const st=window.lastStatus;
  const sm=st&&st.symbols&&st.symbols.find(x=>x.symbol===sym&&x.in_position);
  if(sm&&sm.position){const p=sm.position;
    const hl=(v,col,dash)=>({type:'line',label:'',data:[{x:x0,y:v},{x:x1,y:v}],
      borderColor:col,borderWidth:1,pointRadius:0,borderDash:dash||[]});
    ds.push(hl(p.entry,'#94a3b8',[4,3]),hl(p.sl,'#ef4444'),hl(p.tp,'#22c55e'),hl(p.liq,'#b91c1c',[2,2]));
  }
  const opts={plugins:{legend:{display:false},tooltip:{enabled:true}},
    scales:{x:{type:'time',grid:{display:false},ticks:{color:'#8aa0c0',maxRotation:0}},
            y:{grid:{color:'#243049'},ticks:{color:'#8aa0c0'}}}};
  if(pxChart){pxChart.data.datasets=ds;pxChart.update();}
  else pxChart=new Chart(document.getElementById('px'),{type:'candlestick',data:{datasets:ds},options:opts});
  if(d.rsi){
    const rd=d.rsi.map((y,i)=>({x:d.bars[i].x,y}));
    const flat=v=>[{x:x0,y:v},{x:x1,y:v}];
    const rds=[{label:'RSI',data:rd,borderColor:'#06b6d4',borderWidth:1.2,pointRadius:0,tension:.2},
      {label:'',data:flat(70),borderColor:'#ef4444',borderWidth:.6,pointRadius:0,borderDash:[3,3]},
      {label:'',data:flat(30),borderColor:'#22c55e',borderWidth:.6,pointRadius:0,borderDash:[3,3]}];
    const ropts={plugins:{legend:{display:false},tooltip:{enabled:false}},
      scales:{x:{type:'time',display:false},y:{min:0,max:100,grid:{color:'#243049'},ticks:{color:'#8aa0c0',stepSize:50}}}};
    if(rsiChart){rsiChart.data.datasets=rds;rsiChart.update();}
    else rsiChart=new Chart(document.getElementById('rsi'),{type:'line',data:{datasets:rds},options:ropts});
  }
}
async function closePos(sym){
  if(!confirm('Tutup paksa posisi '+sym+'? (diproses ≤1 siklus)'))return;
  await fetch('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym})});
  loadStatus();
}
async function closeAll(){
  if(!confirm('Tutup SEMUA posisi? (diproses ≤1 siklus)'))return;
  await fetch('/api/close-all',{method:'POST'});
  loadStatus();
}
document.getElementById('chartsym').addEventListener('change',loadChart);
document.getElementById('charttf').addEventListener('change',loadChart);
document.getElementById('vbtn').addEventListener('click',async()=>{
  const body={key:document.getElementById('vkey').value.trim(),secret:document.getElementById('vsecret').value.trim()};
  const el=document.getElementById('vres'); el.textContent='memvalidasi…';
  try{
    const r=await (await fetch('/api/validate-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    el.innerHTML=r.valid?`<span class="pos">VALID — saldo $${f(r.balance_usdc,2)}</span>`:`<span class="neg">INVALID: ${r.error||'gagal'}</span>`;
  }catch(e){el.innerHTML='<span class="neg">error koneksi</span>';}
});
document.getElementById('leverage').addEventListener('input',e=>{
  const lev=+e.target.value||1; riskWarn(lev, (Math.max(1/lev-0.005,0.0005)*100).toFixed(3));
});
document.getElementById('save').addEventListener('click',async()=>{
  const body={
    enabled:document.getElementById('enabled').value==='true',
    technique:document.getElementById('technique').value,
    symbols:document.getElementById('symbols').value,
    leverage:+document.getElementById('leverage').value,
    bet_usd:+document.getElementById('bet_usd').value,
    balance_usd:+document.getElementById('balance_usd').value,
    target_profit_pct:+document.getElementById('target_profit_pct').value,
  };
  const s=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('tf').value=s.timeframe;
  riskWarn(s.leverage, s.liq_pct);
  const el=document.getElementById('saved'); el.textContent=' tersimpan ✓ (bot menerapkan tiap siklus)';
  setTimeout(()=>el.textContent='',4000);
});
async function loadStatus(){
  const a=await (await fetch('/api/account')).json();
  const s=await (await fetch('/api/status')).json();
  window.lastStatus=s;
  const api=a.api_valid===true?'<span class="pos">VALID</span>':(a.api_valid===false?'<span class="neg">INVALID</span>':'paper (tanpa key)');
  let bal=a.balance_usdc!=null?('$'+f(a.balance_usdc,2)):(s.balance_usd!=null?('$'+f(s.balance_usd,2)+' <span class="sub">paper</span>'):'—');
  document.getElementById('acct').innerHTML=
    `Mode: <b>${a.mode}</b> · API: ${api} · Saldo: <b>${bal}</b> · `+
    `Gemini: ${a.gemini_enabled?('<span class="pos">on</span>, '+a.gemini_keys+' key'):'<span class="sub">off</span>'}`+
    (a.error?`<div class="danger">${a.error}</div>`:'');
  if(!s.ts){
    document.getElementById('botstatus').innerHTML='<div class="empty">Bot belum jalan — `python forwardtest.py --poll 30 --use-store`</div>';
    document.getElementById('pairs').innerHTML='';return;
  }
  const nv=s.news_veto&&s.news_veto.active?`<span class="neg">VETO (${s.news_veto.note})</span>`:'<span class="pos">clear</span>';
  document.getElementById('botstatus').innerHTML=
    `Status: ${s.enabled?'<span class="pos">ON</span>':'<span class="neg">OFF</span>'} · Teknik: <b>${s.technique}</b> · `+
    `TF: ${s.timeframe} · Leverage: <b>${s.leverage}x</b> · Bet: $${f(s.bet_usd,2)} · Saldo: <b>$${f(s.balance_usd,2)}</b> · `+
    `Posisi: ${s.open_count}/${s.max_open} · News: ${nv} · <span class="sub">update ${(s.ts||'').slice(11,19)} UTC</span>`;
  document.getElementById('pairs').innerHTML=table(
    [{t:'Pair',k:'symbol'},
     {t:'Harga',f:r=>r.price!=null?f(r.price,4):'—'},
     {t:'ATR%',f:r=>r.atr_pct!=null?f(r.atr_pct,2):'—'},
     {t:'Sinyal',f:r=>r.signal||'—',cls:r=>r.signal==='LONG'?'pos':(r.signal==='SHORT'?'neg':'')},
     {t:'Posisi (PnL)',f:r=>r.in_position?`${r.position.side.toUpperCase()} ${(r.position.pnl_usd>=0?'+':'')+f(r.position.pnl_usd,2)}`:'—',
      cls:r=>r.in_position?(r.position.pnl_usd>=0?'pos':'neg'):''},
     {t:'Keterangan',f:r=>r.blocked||'—'},
     {t:'Aksi',f:r=>r.in_position?`<button class="btnsm" onclick="closePos('${r.symbol}')">Close</button>`:'—'}],
    s.symbols||[]);
}
loadSettings();
function refresh(){load();loadStatus();loadChart();}
refresh();setInterval(refresh,10000);
</script></body></html>"""

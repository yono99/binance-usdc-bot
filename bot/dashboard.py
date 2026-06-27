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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
  if(chart){chart.data.labels=labels;chart.data.datasets[0].data=data;chart.update()}
  else chart=new Chart(ctx,{type:'line',data:{labels,datasets:[{data,borderColor:'#6366f1',
    backgroundColor:'rgba(99,102,241,.15)',fill:true,tension:.25,pointRadius:0,borderWidth:2}]},
    options:{plugins:{legend:{display:false}},scales:{x:{display:false},
      y:{grid:{color:'#243049'},ticks:{color:'#8aa0c0'}}}}});
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
}
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
loadSettings();
load();setInterval(load,10000);
</script></body></html>"""

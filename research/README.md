# research/ — harness di luar runtime entry

Skrip di folder ini **bukan** bagian loop trading production (`forwardtest.py`).
Mereka mencari / menolak hipotesis (edge hunt, H28/H30, cycle dump, alpha once-off).

## Menjalankan

Selalu dari **akar repo** (path `data/`, `logs/` relatif ke root):

```bash
# contoh
python research/edge_hunt.py --round all --out logs/edge_hunt.json
python research/edge_hunt_risk_filter.py
python research/cyc01_dump_weakness.py
python research/h28_eval.py
```

Sibling import (`from edge_hunt import …`) di-bootstrap lewat `sys.path` di tiap skrip putaran.

## Isi utama

| Grup | File | Catatan |
|---|---|---|
| Edge hunt | `edge_hunt*.py`, `edge_hunt_validate_*.py` | ~300 arms; PROMOTE_PAPER=0 |
| Cycle / ilmu | `cyc01*.py`, `cyc02*.py` | dukung CE-STANCE, bukan auto-short |
| H28 / H30 | `h28_*.py`, `h30_*.py` | paper-test beku; bukan PM2 bot utama |
| Alpha once-off | `*_alpha.py`, `carry*.py`, `statarb.py`, … | arsip eksperimen |

## Scoreboard & loop memori

- [memory/EDGE_HUNT.md](../memory/EDGE_HUNT.md)  
- [memory/EDGE_HUNT_LOOP.md](../memory/EDGE_HUNT_LOOP.md) · [EDGE_HUNT_STATE.json](../memory/EDGE_HUNT_STATE.json)  
- [EDGE_RISET_STATUS.md](EDGE_RISET_STATUS.md) — status kampanye all-time  
- [RESEARCH_LOG.md](../RESEARCH_LOG.md)  
- Hasil JSON: `logs/edge_hunt*.json`  
- Unduh: `download_snap_alltime.py` · coverage: `_snap_coverage.py`

## Jangan

- Wire arm hunt ke `ForwardTester._open_usd` tanpa PROMOTE_PAPER + PR sadar  
- Klaim profit dari in-sample  
- Retread H24–H32 / crash-bounce murni tanpa novelty  

Production architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)

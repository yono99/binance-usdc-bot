"""Fase 3 — H13 sektor/narrative rotation: clustering rolling-correlation + lead-lag.

Hipotesis: pair crypto bergerak dalam KLASTER naratif (L1, meme, AI, DeFi...). Di
tiap klaster ada LEADER (paling likuid — dollar-volume terbesar); saat leader
bergerak, FOLLOWER menyusul dengan lag. Skor follower = return trailing leadernya
→ dipasang ke engine skor generik (xsectional.walk_forward_scores): skor tinggi=long.

Berbeda dari H2 BTC-lead-lag (DITOLAK): di sini leader ditemukan PER-KLASTER via
korelasi rolling, bukan diasumsikan BTC untuk semua — menangkap rotasi naratif
yang tak terlihat dari sudut BTC-sentris.

Disiplin causal:
- Korelasi & klaster di bar t hanya pakai return window trailing ≤ t.
- Leader dipilih dari dollar-volume trailing ≤ t.
- Skor di t hanya pakai harga ≤ t; forward PnL (> t) urusan engine.
- Leader sendiri & klaster kecil (<3 anggota) diberi NaN (tak ikut ranking).
"""
from __future__ import annotations

import numpy as np

from bot.xs_signals import returns_panel


def corr_matrix(r_window: np.ndarray) -> np.ndarray:
    """Korelasi antar-kolom dari window return [W×N]. Kolom tanpa variansi → NaN
    (di-treat sebagai tak-berkorelasi oleh clustering)."""
    W, N = r_window.shape
    x = np.where(np.isfinite(r_window), r_window, 0.0)
    x = x - x.mean(axis=0)
    sd = x.std(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = x / np.where(sd > 0, sd, np.nan)
        C = (z.T @ z) / W
    return C


def greedy_clusters(corr: np.ndarray, threshold: float) -> np.ndarray:
    """Clustering greedy deterministik tanpa dependency: seed = aset dgn tetangga
    ber-korelasi ≥ threshold terbanyak; klaster = seed + semua tetangganya; ulangi
    pada sisa. Kembalikan label [N] (int, mulai 0)."""
    N = corr.shape[0]
    labels = np.full(N, -1, dtype=int)
    unassigned = list(range(N))
    cid = 0
    while unassigned:
        best_seed, best_cnt = unassigned[0], -1
        for i in unassigned:
            cnt = sum(1 for j in unassigned
                      if j != i and np.isfinite(corr[i, j]) and corr[i, j] >= threshold)
            if cnt > best_cnt:
                best_seed, best_cnt = i, cnt
        members = [best_seed] + [j for j in unassigned
                                 if j != best_seed and np.isfinite(corr[best_seed, j])
                                 and corr[best_seed, j] >= threshold]
        for m in members:
            labels[m] = cid
        unassigned = [u for u in unassigned if u not in members]
        cid += 1
    return labels


def score_sector_leadlag(close: np.ndarray, vol: np.ndarray, corr_window: int,
                         lead_lookback: int, threshold: float = 0.6,
                         refresh: int = 10, min_cluster: int = 3) -> np.ndarray:
    """Panel skor [T×N] H13. Untuk tiap follower: skor[t] = return trailing
    lead_lookback bar dari LEADER klasternya. Leader & anggota klaster kecil = NaN.

    refresh: klaster & leader dihitung ulang tiap `refresh` bar (hemat komputasi;
    tetap causal karena hanya pakai data ≤ t saat dihitung)."""
    T, N = close.shape
    r = returns_panel(close)
    dvol = close * vol
    score = np.full((T, N), np.nan)
    labels, leaders = None, {}
    for t in range(corr_window, T - 0):
        if labels is None or (t - corr_window) % refresh == 0:
            w = r[t - corr_window + 1:t + 1]
            C = corr_matrix(w)
            labels = greedy_clusters(C, threshold)
            leaders = {}
            for c in np.unique(labels):
                idx = np.where(labels == c)[0]
                if len(idx) < min_cluster:
                    leaders[int(c)] = None
                    continue
                mv = np.nanmean(dvol[t - corr_window + 1:t + 1][:, idx], axis=0)
                leaders[int(c)] = int(idx[int(np.nanargmax(mv))])
        if t - lead_lookback < 0:
            continue
        for i in range(N):
            ld = leaders.get(int(labels[i]))
            if ld is None or ld == i:
                continue
            prev = close[t - lead_lookback, ld]
            if np.isfinite(prev) and prev > 0:
                score[t, i] = close[t, ld] / prev - 1.0
    return score

import { useState } from "react";
import { api, f } from "../api";
import type { Account } from "../types";

export function AccountPanel({ acct }: { acct: Account | null }) {
  const [vkey, setVkey] = useState("");
  const [vsecret, setVsecret] = useState("");
  const [vres, setVres] = useState<JSX.Element | string>("");
  const [tgres, setTgres] = useState<JSX.Element | string>("");

  const apiBadge = !acct ? (
    "—"
  ) : acct.api_valid === true ? (
    <span className="pos">VALID</span>
  ) : acct.api_valid === false ? (
    <span className="neg">INVALID</span>
  ) : (
    "paper (tanpa key)"
  );
  // Saldo per-wallet (USDT-M / USDC-M) — live: dari Binance Futures; paper: —
  const hasBal = acct?.balance_usdc != null || acct?.balance_usdt != null;
  const balLine = !hasBal
    ? "—"
    : `USDT $${f(acct?.balance_usdt, 2)} · USDC $${f(acct?.balance_usdc, 2)}` +
      (acct?.balance_total != null ? ` · Total $${f(acct.balance_total, 2)}` : "");

  const validate = async () => {
    setVres("memvalidasi…");
    try {
      const r = await api.validateKey(vkey.trim(), vsecret.trim());
      setVres(
        r.valid ? (
          <span className="pos">
            VALID — saldo USDT ${f(r.balance_usdt, 2)} · USDC ${f(r.balance_usdc, 2)}
          </span>
        ) : (
          <span className="neg">INVALID: {r.error || "gagal"}</span>
        )
      );
    } catch {
      setVres(<span className="neg">error koneksi</span>);
    }
  };

  const testTelegram = async () => {
    setTgres("mengirim…");
    try {
      const r = await api.notifyTest();
      setTgres(
        r.ok ? <span className="pos">terkirim ✓ (cek Telegram)</span> : <span className="neg">{r.error || "gagal"}</span>
      );
    } catch {
      setTgres(<span className="neg">error koneksi</span>);
    }
  };

  return (
    <div className="panel">
      <h2>Akun / API</h2>
      <div className="line">
        Mode: <b>{acct?.mode ?? "—"}</b> · API: {apiBadge}
        {hasBal && <> · Saldo: <b>{balLine}</b></>} · Gemini:{" "}
        {acct?.gemini_enabled ? (
          <span className="pos">on, {acct.gemini_keys} key</span>
        ) : (
          <span className="sub">off</span>
        )}
        {acct?.error && <div className="danger">{acct.error}</div>}
      </div>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>
          API Key (validasi)
          <input value={vkey} onChange={(e) => setVkey(e.target.value)} placeholder="kosong = pakai .env live" />
        </label>
        <label>
          API Secret
          <input type="password" value={vsecret} onChange={(e) => setVsecret(e.target.value)} placeholder="kosong = pakai .env live" />
        </label>
      </div>
      <button onClick={validate}>Validasi API Key</button> <span className="sub">{vres}</span>
      <div style={{ marginTop: 8 }}>
        <button onClick={testTelegram}>Test Telegram</button> <span className="sub">{tgres}</span>
      </div>
    </div>
  );
}

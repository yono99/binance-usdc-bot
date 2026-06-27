import { useState } from "react";

/** Multi-select pair dengan chip + pencarian (untuk pair yang ditradingkan). */
export function PairPicker({
  value,
  options,
  onChange,
}: {
  value: string[];
  options: string[];
  onChange: (v: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const avail = options
    .filter((o) => !value.includes(o) && o.toLowerCase().includes(q.toLowerCase()))
    .slice(0, 60);
  const add = (s: string) => {
    onChange([...value, s]);
    setQ("");
  };
  const remove = (s: string) => onChange(value.filter((x) => x !== s));

  return (
    <div>
      <div className="chips">
        {value.map((s) => (
          <span className="chip" key={s}>
            {s}
            <button type="button" onClick={() => remove(s)} title="hapus">
              ×
            </button>
          </span>
        ))}
        {!value.length && <span className="sub">belum ada pair — cari & tambah di bawah</span>}
      </div>
      <input
        className="ss-search"
        style={{ marginTop: 6 }}
        placeholder="cari & tambah pair…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {q && (
        <div className="ss-list" style={{ maxHeight: 180 }}>
          {avail.map((o) => (
            <div key={o} className="ss-opt" onClick={() => add(o)}>
              + {o}
            </div>
          ))}
          {!avail.length && <div className="ss-empty">tak ada hasil</div>}
        </div>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";

/** Dropdown single-select dengan kotak pencarian. */
export function SearchSelect({
  value,
  options,
  onChange,
  placeholder = "pilih…",
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const filtered = options.filter((o) => o.toLowerCase().includes(q.toLowerCase())).slice(0, 60);

  return (
    <div className="ss" ref={ref}>
      <button type="button" className="ss-btn" onClick={() => setOpen((o) => !o)}>
        {value || placeholder} <span style={{ float: "right" }}>▾</span>
      </button>
      {open && (
        <div className="ss-pop">
          <input
            autoFocus
            className="ss-search"
            placeholder="cari…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="ss-list">
            {filtered.map((o) => (
              <div
                key={o}
                className={"ss-opt" + (o === value ? " sel" : "")}
                onClick={() => {
                  onChange(o);
                  setOpen(false);
                  setQ("");
                }}
              >
                {o}
              </div>
            ))}
            {!filtered.length && <div className="ss-empty">tak ada hasil</div>}
          </div>
        </div>
      )}
    </div>
  );
}

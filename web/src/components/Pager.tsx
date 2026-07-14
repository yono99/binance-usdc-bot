const SIZES = [5, 10, 20, 30, 100];

function pageList(cur: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const lo = Math.max(2, cur - 1);
  const hi = Math.min(total - 1, cur + 1);
  if (lo > 2) out.push("…");
  for (let i = lo; i <= hi; i++) out.push(i);
  if (hi < total - 1) out.push("…");
  out.push(total);
  return out;
}

export function Pager({
  total,
  page,
  size,
  onPage,
  onSize,
}: {
  total: number;
  page: number;
  size: number;
  onPage: (p: number) => void;
  onSize: (s: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const cur = Math.min(page, pages);
  const from = total === 0 ? 0 : (cur - 1) * size + 1;
  const to = Math.min(cur * size, total);

  return (
    <div className="pager">
      <span className="sub">
        {from}–{to} dari {total}
      </span>
      <span className="sub">· per halaman:</span>
      {SIZES.map((s) => (
        <button
          key={s}
          className={"pg" + (s === size ? " sel" : "")}
          onClick={() => {
            onSize(s);
            onPage(1);
          }}
        >
          {s}
        </button>
      ))}
      <span className="pg-nums">
        <button className="pg" disabled={cur <= 1} onClick={() => onPage(cur - 1)}>
          ‹
        </button>
        {pageList(cur, pages).map((p, i) =>
          p === "…" ? (
            <span key={`e${i}`} className="sub">
              …
            </span>
          ) : (
            <button key={p} className={"pg" + (p === cur ? " sel" : "")} onClick={() => onPage(p)}>
              {p}
            </button>
          )
        )}
        <button className="pg" disabled={cur >= pages} onClick={() => onPage(cur + 1)}>
          ›
        </button>
      </span>
    </div>
  );
}

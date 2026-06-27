import type { ReactNode } from "react";

export interface Col<T> {
  t: ReactNode;
  render: (r: T) => ReactNode;
  cls?: (r: T) => string;
}

export function Table<T>({
  cols,
  rows,
  rowCls,
  empty = "Belum ada data — jalankan forwardtest.py",
}: {
  cols: Col<T>[];
  rows: T[];
  rowCls?: (r: T) => string;
  empty?: string;
}) {
  if (!rows.length) return <div className="empty">{empty}</div>;
  return (
    <table>
      <thead>
        <tr>
          {cols.map((c, i) => (
            <th key={i}>{c.t}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri} className={rowCls ? rowCls(r) : ""}>
            {cols.map((c, ci) => (
              <td key={ci} className={c.cls ? c.cls(r) : ""}>
                {c.render(r)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

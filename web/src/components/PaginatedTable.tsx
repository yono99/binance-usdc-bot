import { useState } from "react";
import { Table, type Col } from "./Table";
import { Pager } from "./Pager";

/** Table + Pager: paginasi sisi-klien (10/20/30/100 + nomor halaman). */
export function PaginatedTable<T>({
  cols,
  rows,
  rowCls,
  empty,
  initialSize = 5,
}: {
  cols: Col<T>[];
  rows: T[];
  rowCls?: (r: T) => string;
  empty?: string;
  initialSize?: number;
}) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(initialSize);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const cur = Math.min(page, pages);
  const slice = rows.slice((cur - 1) * size, cur * size);
  return (
    <>
      <Table cols={cols} rows={slice} rowCls={rowCls} empty={empty} />
      {rows.length > size && (
        <Pager total={rows.length} page={page} size={size} onPage={setPage} onSize={setSize} />
      )}
    </>
  );
}

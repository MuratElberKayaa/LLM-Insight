import type { ReactNode } from "react";

export interface Column<Row> {
  key: string;
  header: ReactNode;
  align?: "left" | "right";
  render: (row: Row) => ReactNode;
  /** Row header cell (one per row, for screen readers). */
  rowHeader?: boolean;
}

/**
 * Accessible table: a caption (visible or screen-reader only), `scope` on headers, one row
 * header per row, horizontal scrolling inside its own region on narrow screens.
 */
export function DataTable<Row>({
  caption,
  captionHidden = false,
  columns,
  rows,
  rowKey,
  empty = "No rows.",
  testId,
  fit = false,
}: {
  caption: string;
  captionHidden?: boolean;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
  empty?: ReactNode;
  testId?: string;
  /**
   * Fit the container instead of scrolling: cells wrap and the columns share the width. For
   * tables whose cells wrap well; their width then never depends on the browser's fonts.
   */
  fit?: boolean;
}) {
  return (
    // `relative`: the scroll region must be the containing block of absolutely positioned
    // descendants (the sr-only labels), or they escape its clipping and widen the page.
    <div className="relative overflow-x-auto rounded-lg border border-line" role="region" aria-label={caption} tabIndex={0} data-testid={testId}>
      <table className={`w-full border-collapse text-sm ${fit ? "" : "min-w-max"}`} data-fit={fit ? "true" : undefined}>
        <caption className={captionHidden ? "sr-only" : "px-3 py-2 text-left text-xs text-ink-3"}>{caption}</caption>
        <thead className="bg-raised">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`${fit ? "whitespace-normal align-bottom" : "whitespace-nowrap"} px-3 py-2 text-xs font-medium text-ink-2 ${c.align === "right" ? "text-right" : "text-left"}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-4 text-center text-ink-3">
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={rowKey(row, i)} className="border-t border-line">
                {columns.map((c) => {
                  const cls = `${fit ? "whitespace-normal break-words align-top" : "whitespace-nowrap"} px-3 py-2 ${c.align === "right" ? "tabular text-right" : "text-left"} ${c.rowHeader ? "font-medium text-ink" : "text-ink-2"}`;
                  return c.rowHeader ? (
                    <th key={c.key} scope="row" className={cls}>
                      {c.render(row)}
                    </th>
                  ) : (
                    <td key={c.key} className={cls}>
                      {c.render(row)}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Muted text for a value the backend reported as null. */
export function NullValue({ children = "—", label = "not reported" }: { children?: ReactNode; label?: string }) {
  return (
    <span className="text-ink-3" title={label}>
      {children}
      <span className="sr-only"> ({label})</span>
    </span>
  );
}

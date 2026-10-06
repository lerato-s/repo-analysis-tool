/** Dashboard filter state and mapping to the API filter body. */
import type { FilterBody, Granularity, Mode } from "./api";

export interface FilterState {
  mode: Mode;
  fromTs: number | null; // inclusive, seconds
  toTs: number | null; // exclusive, seconds
  hashes: string[];
  authorKeys: number[];
  path: string; // object scope ("" = whole repository)
  granularity: Granularity;
}

export const EMPTY_FILTER: FilterState = {
  mode: "all",
  fromTs: null,
  toTs: null,
  hashes: [],
  authorKeys: [],
  path: "",
  granularity: "auto",
};

export const toBody = (f: FilterState): Partial<FilterBody> => ({
  mode: f.mode,
  from_ts: f.fromTs,
  to_ts: f.toTs,
  hashes: f.hashes,
  author_keys: f.authorKeys,
  path: f.path,
  granularity: f.granularity,
});

export const filterKey = (f: FilterState): string => JSON.stringify(toBody(f));

// datetime-local <-> unix seconds helpers
export function tsToInput(ts: number | null): string {
  if (ts == null) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function inputToTs(v: string): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? Math.floor(t / 1000) : null;
}

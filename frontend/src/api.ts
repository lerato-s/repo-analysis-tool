/** Typed client for the RAT backend REST API (same-origin `/api`). */

export type RepoStatus =
  | "pending"
  | "cloning"
  | "extracting"
  | "analyzing"
  | "ready"
  | "error";
export type Mode = "all" | "range" | "list";
export type Granularity = "auto" | "day" | "week" | "month";

export interface Repo {
  id: number;
  name: string;
  kind: "zip" | "clone";
  origin: string;
  origin_label: string;
  status: RepoStatus;
  phase: string | null;
  progress: number;
  error: string | null;
  branch: string | null;
  head_hash: string | null;
  commit_count: number;
  change_count: number;
  file_count: number;
  binary_count: number;
  obj_count: number;
  first_commit_ts: number | null;
  last_commit_ts: number | null;
  mailmap_kind: string | null;
  created_ts: number;
  ready_ts: number | null;
  data_version: number;
}

export interface MetricSet {
  added: number;
  removed: number;
  growth: number;
  churn: number;
  mods: number;
  freq: number;
  rate: number;
}

export interface ObjectMetrics extends MetricSet {
  path: string;
  is_file: boolean;
  file_count?: number;
}

export interface FileRow extends MetricSet {
  path: string;
  is_file: boolean;
  in_head: boolean;
  ever_binary: boolean;
  is_gitlink: boolean;
}

export interface DirRow extends MetricSet {
  path: string;
  is_file: boolean;
  file_count: number;
}

export interface TopAuthor {
  key: number;
  label: string;
  churn: number;
  ownership: number;
}

export type ObjectsRow = (FileRow | DirRow) & { tops: TopAuthor[] };

export interface Alias {
  name: string;
  email: string;
  commits: number;
}

export interface AuthorAgg {
  key: number;
  label: string;
  emails: string[];
  aliases: Alias[];
  commits: number;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  mods: number;
  ownership: number;
}

export interface TimelinePoint {
  b: string;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  commits: number;
}

export interface Analytics {
  repo: Repo;
  filter: {
    mode: Mode;
    from_ts: number | null;
    to_ts: number | null;
    author_keys: number[];
    path: string;
    hashes_count: number;
  };
  kpis: {
    n_commits: number;
    object: ObjectMetrics;
    repo_totals: { added: number; removed: number; growth: number; churn: number };
    file_count: number;
    dir_count: number;
    author_count: number;
    first_ts: number | null;
    last_ts: number | null;
  };
  authors: AuthorAgg[];
  dirs: DirRow[];
  top_files: FileRow[];
  timeline: { granularity: "day" | "week" | "month"; points: TimelinePoint[] };
  author_timeline: {
    authors: { key: number; label: string }[];
    points: { b: string; values: number[] }[];
  };
  heatmap: {
    buckets: string[];
    files: string[];
    values: [number, number, number][];
  };
  truncated: boolean;
}

export interface CommitRow {
  hash: string;
  short: string;
  ts: number;
  subject: string;
  added: number;
  removed: number;
  file_count: number;
  parent_hash: string | null;
  author_name: string;
  author_email: string;
  author_label: string;
}

export interface CommitDetail {
  commit: {
    hash: string;
    short: string;
    ts: number;
    subject: string;
    parent_hash: string | null;
    added: number;
    removed: number;
    file_count: number;
  };
  author: {
    label: string;
    name: string;
    email: string;
    canonical_name: string;
    canonical_email: string;
  };
  files: {
    path: string;
    old_path: string | null;
    added: number;
    removed: number;
    binary: boolean;
    is_rename: boolean;
  }[];
}

export interface AuthorGroup {
  key: number;
  label: string;
  emails: string[];
  aliases: Alias[];
  commits: number;
  churn: number;
  ownership: number;
}

export interface MergeOp {
  id: number;
  label: string;
  members: [string, string][];
  created_ts: number;
}

export interface AuthorsPayload {
  mailmap_kind: string | null;
  groups: AuthorGroup[];
  identity_count: number;
  ops: MergeOp[];
}

export interface FilterBody {
  mode: Mode;
  from_ts: number | null;
  to_ts: number | null;
  hashes: string[];
  author_keys: number[];
  path: string;
  granularity: Granularity;
}

export interface ObjectQuery {
  kind?: "file" | "dir";
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  offset?: number;
  limit?: number;
}

export interface CommitQuery {
  q?: string;
  author_keys?: string;
  offset?: number;
  limit?: number;
  order?: "asc" | "desc";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the RAT backend — is it running?");
  }
  if (!res.ok) {
    let detail = `Request failed (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg)
        detail = String(body.detail[0].msg);
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  listRepos: () => request<{ repos: Repo[] }>("/api/repos").then((r) => r.repos),
  getRepo: (id: number) => request<Repo>(`/api/repos/${id}`),
  uploadRepo: (file: File, name?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    return request<Repo>("/api/repos/upload", { method: "POST", body: fd });
  },
  cloneRepo: (url: string, name?: string) =>
    request<Repo>("/api/repos/clone", json({ url, name: name || null })),
  reanalyze: (id: number, full = false) =>
    request<{ ok: boolean; full: boolean }>(
      `/api/repos/${id}/reanalyze?full=${full}`,
      { method: "POST" },
    ),
  deleteRepo: (id: number) =>
    request<{ ok: boolean }>(`/api/repos/${id}`, { method: "DELETE" }),

  analytics: (id: number, body: Partial<FilterBody>) =>
    request<Analytics>(`/api/repos/${id}/analytics`, json(body)),
  objects: (id: number, body: Partial<FilterBody>, query: ObjectQuery = {}) =>
    request<{ kind: string; total: number; rows: ObjectsRow[] }>(
      `/api/repos/${id}/objects${qs(query as Record<string, string | number>)}`,
      json(body),
    ),
  commits: (id: number, query: CommitQuery = {}) =>
    request<{ total: number; rows: CommitRow[] }>(
      `/api/repos/${id}/commits${qs(query as Record<string, string | number>)}`,
    ),
  commitHashes: (
    id: number,
    query: { q?: string; author_keys?: string; cap?: number } = {},
  ) =>
    request<{ hashes: string[]; total: number; truncated: boolean }>(
      `/api/repos/${id}/commits/hashes${qs(query as Record<string, string | number>)}`,
    ),
  commitDetail: (id: number, hash: string) =>
    request<CommitDetail>(
      `/api/repos/${id}/commits/${encodeURIComponent(hash)}`,
    ),

  authors: (id: number) => request<AuthorsPayload>(`/api/repos/${id}/authors`),
  mergeAuthors: (id: number, label: string, members: [string, string][]) =>
    request<AuthorsPayload>(
      `/api/repos/${id}/authors/merge`,
      json({ label, members }),
    ),
  deleteMergeOp: (id: number, opId: number) =>
    request<AuthorsPayload>(`/api/repos/${id}/authors/ops/${opId}`, {
      method: "DELETE",
    }),
};

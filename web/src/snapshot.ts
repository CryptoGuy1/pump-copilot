import { useQuery } from "@tanstack/react-query";

/** Step 7b: the static snapshot build (`npm run build:snapshot`, for GitHub Pages). The app
 * reads the API's recorded responses from snapshot/ instead of calling the API; nothing is
 * live, no stream is opened, and every action is disabled. In the normal build these are off. */
export const SNAPSHOT = import.meta.env.VITE_SNAPSHOT === "1";
export const ACTIONS_DISABLED = "Static snapshot: actions are disabled";
export const NOT_IN_SNAPSHOT = "not_in_snapshot";
const BASE = import.meta.env.BASE_URL;  // "/pump-copilot/" in the snapshot build

/** The snapshot file for one GET request (the same rule as pumpcopilot.snapshot.key). */
export function snapshotKey(path: string, query: Record<string, unknown> = {}): string {
  const q = Object.entries(query).filter(([, v]) => v != null)
    .map(([k, v]) => [k, String(v)] as const).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const base = path.replace(/^\/+|\/+$/g, "");
  return base + (q.length ? "@" + q.map(([k, v]) => `${k}=${v}`).join("~") : "") + ".json";
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body),
  { status, headers: { "Content-Type": "application/json" } });

/** The client's fetch in the snapshot build: a GET reads its snapshot file; anything else, or
 * a request with no file, answers with the API's error shape. */
export async function snapshotFetch(input: Request): Promise<Response> {
  const url = new URL(input.url);
  if (input.method !== "GET")
    return json(403, { error: { status: 403, code: "snapshot_read_only",
                                message: ACTIONS_DISABLED } });
  const query = Object.fromEntries(url.searchParams.entries());
  const r = await fetch(`${BASE}snapshot/${snapshotKey(url.pathname, query)}`);
  if (r.ok && (r.headers.get("Content-Type") ?? "").includes("json")) return r;
  return json(404, { error: { status: 404, code: NOT_IN_SNAPSHOT,
                              message: "Not included in this snapshot" } });
}

export interface SnapshotManifest {
  exported_at: string; commit: string; commit_dirty: boolean;
  sessions: { session_id: number; asset_id: string; source_day: string; scenario: string | null;
              synthetic: boolean; status: string }[];
  answers_model: string | null; answers_recorded_at: string | null; answers: number;
  files: number; bytes: number; not_included: string[];
}

export function useSnapshotManifest() {
  return useQuery({ queryKey: ["snapshot-manifest"], enabled: SNAPSHOT, staleTime: Infinity,
    queryFn: async (): Promise<SnapshotManifest> =>
      (await fetch(`${BASE}snapshot/manifest.json`)).json() });
}

export interface RecordedAnswers {
  recorded_at: string; provider: string; model: string; cap: number; requests_used: number;
  answers: { case_id: number; synthetic: boolean; question: string;
             response: import("./api/client").Schema<"AssistantResponse"> }[];
}

export function useRecordedAnswers() {
  return useQuery({ queryKey: ["snapshot-answers"], enabled: SNAPSHOT, staleTime: Infinity,
    queryFn: async (): Promise<RecordedAnswers | null> => {
      const r = await fetch(`${BASE}snapshot/answers.json`);
      return r.ok ? r.json() : null;
    } });
}

/** A date as the snapshot shows it: 1 October 2026. */
export const snapshotDate = (iso: string | null | undefined) => iso
  ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric",
                                                  timeZone: "UTC" }) : "";

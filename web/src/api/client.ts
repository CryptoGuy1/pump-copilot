import createClient from "openapi-fetch";
import { SNAPSHOT, snapshotFetch } from "../snapshot";
import type { components, paths } from "./schema";

/** Typed client generated from api/openapi.json: paths, parameters, request bodies and every
 * response model. The dev server proxies /api to the API on 127.0.0.1; the static snapshot
 * build reads recorded responses instead (src/snapshot.ts). */
export const client = createClient<paths>(SNAPSHOT ? { baseUrl: "", fetch: snapshotFetch }
                                                   : { baseUrl: "" });

/** A response model by name, e.g. Schema<"CaseDetail">. */
export type Schema<K extends keyof components["schemas"]> = components["schemas"][K];

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public details?: unknown) {
    super(message);
  }
}

/** Unwrap a client call: the typed body on success, an ApiError (the API's one error shape)
 * otherwise. */
export async function call<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>
                              ): Promise<T> {
  const { data, error, response } = await p;
  if (!response.ok) {
    const e = (error as { error?: { status: number; code: string; message: string;
                                    details?: unknown } })?.error;
    throw new ApiError(response.status, e?.code ?? "http_error",
                       e?.message ?? response.statusText, e?.details);
  }
  return data as T;
}

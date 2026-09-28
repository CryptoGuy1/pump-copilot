import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Typed client generated from api/openapi.json (paths, parameters and request bodies). The
 * API declares no response models, so response bodies are cast to the interfaces in
 * ./types. The dev server proxies /api to the API on 127.0.0.1. */
export const client = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public details?: unknown) {
    super(message);
  }
}

type Result = { data?: unknown; error?: unknown; response: Response };

/** Unwrap a client call: the body on success, an ApiError (the API's one error shape) if not. */
export async function call<T>(p: Promise<Result>): Promise<T> {
  const { data, error, response } = await p;
  if (!response.ok) {
    const e = (error as { error?: { status: number; code: string; message: string;
                                    details?: unknown } })?.error;
    throw new ApiError(response.status, e?.code ?? "http_error",
                       e?.message ?? response.statusText, e?.details);
  }
  return data as T;
}

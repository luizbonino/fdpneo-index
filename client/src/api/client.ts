/**
 * HTTP access to the index API: base URL, bearer token, JSON, and errors.
 *
 * The server's error body carries `message` (and `errors` for validation);
 * `ApiError` surfaces both so views can show what went wrong.
 */

import { runtimeApiUrl } from "@/runtimeConfig";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type TokenSource = () => string | null;
let tokenSource: TokenSource = () => null;

export function setTokenSource(source: TokenSource): void {
  tokenSource = source;
}

export function apiUrl(path: string): string {
  return `${runtimeApiUrl()}${path}`;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  accept?: string;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: options.accept ?? "application/json" };
  const token = tokenSource();
  if (token) headers.Authorization = `Bearer ${token}`;
  const init: RequestInit = { method: options.method ?? "GET", headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch {
    throw new ApiError(0, "The index cannot be reached.");
  }
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? await response.json() : await response.text()) as T;
}

async function toError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { message?: string; errors?: Record<string, string> };
    return new ApiError(response.status, body.message ?? response.statusText, body.errors ?? {});
  } catch {
    return new ApiError(response.status, response.statusText || `HTTP ${response.status}`);
  }
}

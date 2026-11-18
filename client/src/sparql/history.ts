/**
 * Query history in this browser (localStorage), and shareable query links.
 *
 * History keeps the last 50 runs; pinned entries are kept beyond that.
 * Storage may be unavailable (private windows, blocked site data): every
 * access is guarded and the page works without it.
 *
 * A share link carries the query in the URL: `?q=z.<base64url of deflate-raw>`
 * where the browser can compress, `?q=b.<base64url>` otherwise.
 */

import { ref } from "vue";

export interface HistoryItem {
  query: string;
  at: string;
  rows: number | null;
  pinned?: boolean;
}

const KEY = "fdpindex.sparql.history";
const MAX = 50;

function load(): HistoryItem[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as HistoryItem[]) : [];
  } catch {
    return [];
  }
}

export const history = ref<HistoryItem[]>(load());

function save(): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(history.value));
  } catch {
    /* storage unavailable: keep the in-memory list */
  }
}

export function remember(query: string, rows: number | null): void {
  const text = query.trim();
  const previous = history.value.find((h) => h.query === text);
  const rest = history.value.filter((h) => h.query !== text);
  const item: HistoryItem = {
    query: text,
    at: new Date().toISOString(),
    rows,
    ...(previous?.pinned ? { pinned: true } : {}),
  };
  const pinned = rest.filter((h) => h.pinned);
  const recent = rest.filter((h) => !h.pinned).slice(0, MAX - 1);
  history.value = [item, ...pinned, ...recent];
  save();
}

export function togglePin(query: string): void {
  history.value = history.value.map((h) => (h.query === query ? { ...h, pinned: !h.pinned } : h));
  save();
}

export function clearHistory(): void {
  history.value = history.value.filter((h) => h.pinned);
  save();
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromBase64Url(text: string): Uint8Array {
  const padded = text.replaceAll("-", "+").replaceAll("_", "/");
  const binary = atob(padded + "===".slice((padded.length + 3) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

async function pipe(bytes: Uint8Array, stream: TransformStream<Uint8Array, Uint8Array>) {
  const body = new Blob([bytes as BlobPart]).stream().pipeThrough(stream);
  return new Uint8Array(await new Response(body).arrayBuffer());
}

export async function encodeQuery(query: string): Promise<string> {
  const bytes = new TextEncoder().encode(query);
  if (typeof CompressionStream !== "undefined") {
    try {
      return `z.${toBase64Url(await pipe(bytes, new CompressionStream("deflate-raw") as unknown as TransformStream<Uint8Array, Uint8Array>))}`;
    } catch {
      /* fall through */
    }
  }
  return `b.${toBase64Url(bytes)}`;
}

export async function decodeQuery(encoded: string): Promise<string | null> {
  try {
    const [kind, data] = [encoded.slice(0, 2), encoded.slice(2)];
    const bytes = fromBase64Url(data);
    if (kind === "z.") {
      return new TextDecoder().decode(
        await pipe(
          bytes,
          new DecompressionStream("deflate-raw") as unknown as TransformStream<
            Uint8Array,
            Uint8Array
          >,
        ),
      );
    }
    if (kind === "b.") return new TextDecoder().decode(bytes);
  } catch {
    /* a broken link */
  }
  return null;
}

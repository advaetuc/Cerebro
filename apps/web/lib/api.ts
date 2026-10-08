export type Artist = { name: string; listeners?: string | number | null; url?: string | null };
export type Pick = {
  id: string;
  title: string;
  year: string | null;
  image_url: string | null;
  score: number;
  match_pct: number;
  why: string;
  source_url: string;
  regional?: boolean;
};
export type VibeResult = {
  vector: Record<string, number>;
  signal_pct: number;
  archetype: string;
  secondary: string;
  margin: number;
  top_families: Array<{ id: string; share: number }>;
  movies: Pick[];
  games: Pick[];
  attribution: string[];
  degraded: boolean;
  degraded_reasons?: string[];
};
export type AnalyzeRequest =
  | { mode: "lastfm"; username: string }
  | { mode: "seed"; artists: string[] };

const BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TIMEOUT_MS = 60_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
    const body: unknown = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof body === "object" && body !== null
        ? ("message" in body ? body.message : "detail" in body ? body.detail : null)
        : null;
      throw new Error(typeof detail === "string" ? detail : "We couldn’t complete that request. Try again.");
    }
    return body as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("This is taking longer than expected. Please try again.");
    }
    if (error instanceof TypeError) {
      throw new Error("Cerebro couldn’t reach the API. Check that the backend is running.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function searchArtists(query: string): Promise<{ artists: Artist[] }> {
  return request(`/artists/search?q=${encodeURIComponent(query)}`);
}

export function analyze(input: AnalyzeRequest): Promise<VibeResult> {
  return request("/analyze", { method: "POST", body: JSON.stringify(input) });
}

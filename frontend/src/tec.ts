// TEC map data source (Cloudflare R2), configured per environment.
//
// Resolution order:
//   1. VITE_TEC_BASE_URL — set this per environment (e.g. in Cloudflare Pages/
//      Workers env vars, or a local `.env`). Vite inlines it at BUILD time, so
//      it must be present before `npm run build` runs.
//   2. Fallback: the production custom domain on the R2 `tec` bucket.
//
// The custom domain (data.ionocast.com) is what makes this production-ready:
// proper CDN caching, no r2.dev rate limit, and support for cache/WAF/hotlink
// rules. The old free `pub-<hash>.r2.dev` URL still works but is rate-limited,
// uncacheable, and meant only for local dev/demos.
//
// The bucket is public READ-ONLY (GET on objects only) — safe for these public
// TEC maps; it exposes no upload keys, and R2 egress is free regardless of URL.
//
// Swapping data sources = change only this string (or the env var) — nothing
// about the blobs, key layout, or the backend/scripts upload path changes.
export const TEC_BASE_URL =
  import.meta.env.VITE_TEC_BASE_URL ?? 'https://data.ionocast.com';

export const TEC_NLAT = 71;
export const TEC_NLON = 73;
const CELLS = TEC_NLAT * TEC_NLON;           // 5183 values per hour

// Build "YYYY-MM-DD" from the Date's own components (NOT toISOString, which
// would shift to UTC and could roll the date). The calendar picked these Y/M/D.
function dateKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Cache the *promise* per day so concurrent calls dedupe and re-selecting is instant.
const cache = new Map<string, Promise<Uint16Array>>();

export function fetchTecDay(d: Date): Promise<Uint16Array> {
  const key = dateKey(d);
  let p = cache.get(key);
  if (!p) {
    const url = `${TEC_BASE_URL}/${d.getFullYear()}/${key}.f16.gz`;
    p = fetch(url).then(async (r) => {
      if (!r.ok) throw new Error(`TEC ${key}: HTTP ${r.status}`);
      return new Uint16Array(await r.arrayBuffer()); // browser inflated gzip -> 24*71*73 halfs
    });
    cache.set(key, p);
  }
  return p;
}

export async function fetchTecHour(d: Date, hour: number): Promise<Uint16Array> {
  const day = await fetchTecDay(d);
  return day.subarray(hour * CELLS, (hour + 1) * CELLS); // one 71×73 map (view, no copy)
}
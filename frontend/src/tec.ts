// TEC map data source.
//
// Default is the SAME-ORIGIN path "/tec", served by our own Cloudflare Worker
// (see worker/index.ts), which streams the blobs out of the R2 `tec` bucket.
// Because the browser fetches from its own origin (e.g. www.ionocast.com/tec/…),
// there is NO cross-origin request and therefore no CORS to configure.
//
// Resolution order:
//   1. VITE_TEC_BASE_URL — override per environment. Vite inlines it at BUILD
//      time, so it must be set before `npm run build`. Use this for local `vite`
//      dev, where "/tec" has no handler: point it at a live origin, e.g.
//      VITE_TEC_BASE_URL=https://www.ionocast.com  (or run `wrangler dev`, which
//      serves "/tec" locally via the Worker).
//   2. Fallback: "/tec" (same-origin Worker route) — correct in production.
//
// The bucket is public READ-ONLY; R2 egress is free. Swapping data sources =
// change only this string (or the env var) — nothing about the blobs, key
// layout, or the backend/scripts upload path changes.
export const TEC_BASE_URL =
  import.meta.env.VITE_TEC_BASE_URL ?? '/tec';

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
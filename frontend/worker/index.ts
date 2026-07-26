/// <reference types="@cloudflare/workers-types" />

// Same-origin server for ionocast.
//
// The React app is deployed as static assets (the ASSETS binding). Any request
// that does NOT match a static file falls through to this Worker. We handle
// "/tec/*" by streaming the object straight out of the R2 `tec` bucket, so the
// browser fetches data from its OWN origin (e.g. https://www.ionocast.com/tec/…)
// and CORS never enters the picture. Everything else is delegated back to the
// static-asset server (which also does SPA fallback to index.html).

export interface Env {
  ASSETS: Fetcher;
  TEC_BUCKET: R2Bucket;
}

const TEC_PREFIX = '/tec/';

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname.startsWith(TEC_PREFIX)) {
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        return new Response('Method Not Allowed', { status: 405 });
      }

      // "/tec/2023/2023-01-18.f16.gz" -> R2 key "2023/2023-01-18.f16.gz"
      const key = decodeURIComponent(url.pathname.slice(TEC_PREFIX.length));
      const object = await env.TEC_BUCKET.get(key);
      if (!object) return new Response('Not found', { status: 404 });

      const headers = new Headers();
      object.writeHttpMetadata(headers); // carries over any stored content-type etc.
      headers.set('etag', object.httpEtag);
      // Blobs are stored gzip-compressed (.f16.gz). Declaring the encoding makes
      // the browser inflate them transparently, which is what tec.ts assumes
      // (it treats the response body as already-decompressed f16 halfs).
      headers.set('content-encoding', 'gzip');
      headers.set('content-type', 'application/octet-stream');
      // Per-day files are immutable once published: cache hard at edge + browser.
      headers.set('cache-control', 'public, max-age=31536000, immutable');

      // encodeBody:'manual' => the runtime leaves our already-gzipped body alone
      // instead of trying to re-encode it. Same origin => no Access-Control-* needed.
      return new Response(object.body, { headers, encodeBody: 'manual' });
    }

    // Not a data request: serve the React app / static assets.
    return env.ASSETS.fetch(request);
  },
};

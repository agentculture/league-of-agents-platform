// Cloudflare Worker: serves https://league-of-agents.ai/evals/data/* from the
// evals S3 bucket's evals/ prefix (REST endpoint), Lambda out of the path.
//   /evals/data/X  ->  https://<EVALS_ORIGIN>/evals/X
// Config: env.EVALS_ORIGIN = bucket REST hostname, e.g.
//   league-of-agents-prod-evals-123456789012.s3.us-east-1.amazonaws.com
// The bucket answers 403 (not 404) for a missing key; viewers get 404.
// Cache policy by path (publisher sets the same headers on upload):
//   <eval>/manifest.json            public, max-age=15, must-revalidate
//   .../live/<seat>.json (in-progress seat files)   public, max-age=5
//   .../thinking/*, name.<hex8+>.json (content-hashed)   immutable, 1 year
//   other match files: origin's own header if immutable (finished), else 15 s
const PREFIX = "/evals/data/";
const IMMUTABLE = "public, max-age=31536000, immutable";
const RUNNING = "public, max-age=15, must-revalidate";
const SEAT = "public, max-age=5";

export function cacheControl(key, originValue) {
  if (/(^|\/)manifest\.json$/.test(key)) return RUNNING;
  if (/(^|\/)live\//.test(key)) return SEAT;
  if (/(^|\/)thinking\//.test(key) || /\.[0-9a-f]{8,}\.json$/.test(key)) return IMMUTABLE;
  return /immutable/.test(originValue || "") ? IMMUTABLE : RUNNING;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const rest = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length) : "";
    if (!rest || rest.endsWith("/") || rest.split("/").some((s) => s === ".." || s === ".")) {
      return new Response("not found", { status: 404 });
    }
    const origin = await fetch(`https://${env.EVALS_ORIGIN}/evals/${rest}`, {
      method: request.method,
      headers: { "If-None-Match": request.headers.get("If-None-Match") || "" },
    });
    if (origin.status === 403 || origin.status === 404) {
      return new Response("not found", { status: 404, headers: { "Cache-Control": "no-store" } });
    }
    const headers = new Headers(origin.headers);
    headers.set("Cache-Control", cacheControl(rest, origin.headers.get("Cache-Control")));
    for (const h of ["x-amz-request-id", "x-amz-id-2", "x-amz-version-id", "Set-Cookie"]) {
      headers.delete(h);
    }
    return new Response(origin.body, { status: origin.status, headers });
  },
};

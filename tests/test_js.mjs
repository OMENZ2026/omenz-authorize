import { authorizedFetch, AuthorizationError } from "../js/index.js";

const mk = (decision) => {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push([url, init]);
    const body = url.endsWith("/v1/authorize")
      ? { decision, decline_reasons: ["swapped payTo"], review_reasons: ["unknown payee"], authorization: { amount_usd: 0.01 } }
      : { ok: true };
    return { status: 200, json: async () => body, text: async () => JSON.stringify(body) };
  };
  return { f, calls };
};

let ok = 0;
{
  const { f, calls } = mk("APPROVE");
  const pay = authorizedFetch(f, { maxPriceUsd: 0.05, policy: { daily_cap_usd: 1 } });
  const { response } = await pay("https://x.example/api", { method: "POST" });
  ok += (await response.json()).ok && calls.length === 2 ? 1 : 0;
}
{
  const { f, calls } = mk("DECLINE");
  try { await authorizedFetch(f)("https://x.example/api"); } catch (e) { ok += e instanceof AuthorizationError && e.kind === "DECLINE" && calls.length === 1 ? 1 : 0; }
}
{
  const { f, calls } = mk("REVIEW");
  try { await authorizedFetch(f)("https://x.example/api"); } catch (e) { ok += e.kind === "REVIEW" && calls.length === 1 ? 1 : 0; }
}
{
  const { f, calls } = mk("REVIEW");
  await authorizedFetch(f, { allowReview: true })("https://x.example/api");
  ok += calls.length === 2 ? 1 : 0;
}
console.log(ok, "/ 4");
process.exit(ok === 4 ? 0 : 1);

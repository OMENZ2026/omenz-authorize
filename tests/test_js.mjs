import { authorizedFetch, AuthorizationError, checkoutFetch, CheckoutStopped } from "../js/index.js";

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
{
  const mkc = (decision) => {
    const calls = [];
    const f = async (url, init = {}) => {
      calls.push([url, init]);
      let body;
      if (url.includes("/checkout/open")) body = { decision, checkout_id: "ck_1", reasons: ["r"], pay: { url: "https://seller.example/api" } };
      else if (url.includes("/checkout/close")) body = { verdict: "GOOD_BUY", signed_receipt: "tok", sent: JSON.parse(init.body) };
      else body = { price: 1 };
      const headers = { get: (h) => (h === "payment-response" && url.startsWith("https://seller") ? "eyJ0cmFuc2FjdGlvbiI6IjB4MSJ9" : null) };
      return { status: 200, headers, json: async () => body, text: async () => JSON.stringify(body), clone() { return this; } };
    };
    return { f, calls };
  };
  const { f, calls } = mkc("GO");
  const r = await checkoutFetch(f, { maxPriceUsd: 0.01, budgetKey: "k" })({ q: "token price" });
  ok += r.receipt.verdict === "GOOD_BUY" && r.receipt.sent.payment_response && r.data.price === 1 && calls.length === 3
        && calls[0][1].headers["x-budget-key"] === "k" ? 1 : 0;
  const s = mkc("STOP");
  try { await checkoutFetch(s.f)("https://seller.example/api"); } catch (e) { ok += e instanceof CheckoutStopped && s.calls.length === 1 ? 1 : 0; }
  const c = mkc("GO");
  await checkoutFetch(c.f, { creditId: "c", creditKey: "k" })("https://seller.example/api");
  ok += c.calls[0][0].endsWith("/checkout/open/credit") && c.calls[2][0].endsWith("/checkout/close/credit") ? 1 : 0;
}
console.log(ok, "/ 7");
process.exit(ok === 7 ? 0 : 1);

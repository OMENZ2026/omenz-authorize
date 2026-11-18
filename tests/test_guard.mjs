import { guardFetch, GuardStopped } from "../js/index.js";

const mk = (decision, { throwOnSeller = false, noPick = false } = {}) => {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.startsWith("https://seller") && throwOnSeller) throw new Error("timeout");
    let body;
    if (/\/v1\/guard(\/credit)?$/.test(url)) body = { decision, reasons: ["r"], checkout_id: "ck_1", pay: { url: "https://seller.example/api", pay_to: "0xabc", amount_usd: 0.003 } };
    else if (url.endsWith("/market/job/guarded")) body = noPick ? { pick: null } : { pick: { url: "https://seller.example/api", method: "GET" }, guard: { decision, checkout_id: "ck_2", pay: { url: "https://seller.example/api", pay_to: "0xabc", amount_usd: 0.003 } } };
    else if (url.includes("/checkout/close")) body = { verdict: "GOOD_BUY", sent: JSON.parse(init.body) };
    else if (url.endsWith("/settlement/rescue")) body = { verdict: "STILL_UNCERTAIN", sent: JSON.parse(init.body) };
    else body = { price: 1 };
    const headers = { get: (h) => (h === "payment-response" && url.startsWith("https://seller") ? "eyJ0cmFuc2FjdGlvbiI6IjB4MSJ9" : null) };
    return { status: 200, headers, json: async () => body, text: async () => JSON.stringify(body), clone() { return this; } };
  };
  return { f, calls };
};

let ok = 0;
{ const { f, calls } = mk("GO");
  const r = await guardFetch(f, { payer: "0xme", budgetKey: "k" })("https://seller.example/api", { purpose: "p", quotedPriceUsd: 0.003 });
  const g = JSON.parse(calls[0][1].body);
  ok += r.receipt.verdict === "GOOD_BUY" && r.data.price === 1 && calls.length === 3 && g.payer === "0xme" && g.quoted_price_usd === 0.003
        && calls[0][1].headers["x-budget-key"] === "k" && r.receipt.sent.payment_response ? 1 : 0; }
{ const { f, calls } = mk("CAUTION");
  try { await guardFetch(f)("https://seller.example/api"); } catch (e) { ok += e instanceof GuardStopped && e.kind === "CAUTION" && calls.length === 1 ? 1 : 0; } }
{ const { f, calls } = mk("CAUTION");
  await guardFetch(f, { allowCaution: true })("https://seller.example/api"); ok += calls.length === 3 ? 1 : 0; }
{ const { f, calls } = mk("GO", { throwOnSeller: true });
  try { await guardFetch(f, { payer: "0xme" })("https://seller.example/api"); } catch (e) {
    ok += e.omenzRescue && e.omenzRescue.verdict === "STILL_UNCERTAIN" && e.omenzRescue.sent.pay_to === "0xabc" && calls[2][0].endsWith("/settlement/rescue") ? 1 : 0; } }
{ const { f, calls } = mk("GO");
  const r = await guardFetch(f)({ need: "token price" }); ok += calls[0][0].endsWith("/market/job/guarded") && r.receipt.verdict === "GOOD_BUY" ? 1 : 0; }
{ const { f } = mk("GO", { noPick: true });
  try { await guardFetch(f)({ need: "nothing" }); } catch (e) { ok += e instanceof GuardStopped && e.kind === "STOP" ? 1 : 0; } }
{ const { f, calls } = mk("GO");
  await guardFetch(f, { creditId: "c", creditKey: "k" })("https://seller.example/api");
  ok += calls[0][0].endsWith("/guard/credit") && calls[2][0].endsWith("/checkout/close/credit") ? 1 : 0; }
console.log(ok, "/ 7");
process.exit(ok === 7 ? 0 : 1);

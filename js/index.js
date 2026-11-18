// omenz-authorize (JS): run OMENZ Authorize before every x402 payment your agent makes.
// Pass the x402-paying fetch you already use (e.g. from your x402 SDK). No keys leave your process.
//
//   import { authorizedFetch } from "omenz-authorize";
//   const pay = authorizedFetch(fetchWithPayment, { maxPriceUsd: 0.05, policy: { daily_cap_usd: 5 } });
//   const { response, authorization } = await pay("https://api.example.com/x402/search", { method: "POST", body: JSON.stringify({ q: "..." }) });

export const AUTHORIZE_URL = "https://www.omenz.org/api/tollbooth/v1/authorize";

export class AuthorizationError extends Error {
  constructor(kind, result) {
    const reasons = (kind === "DECLINE" ? result.decline_reasons : result.review_reasons) || [];
    super(`OMENZ Authorize ${kind}: ${reasons.join("; ") || "no reason given"}`);
    this.kind = kind;
    this.result = result;
  }
}

export async function authorize(payingFetch, url, method = "GET", checks = {}, authorizeUrl = AUTHORIZE_URL) {
  const body = { url, method: method.toUpperCase() };
  for (const [k, v] of Object.entries(checks)) if (v !== undefined && v !== null) body[k] = v;
  const r = await payingFetch(authorizeUrl, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 402) throw new Error("the fetch did not pay the OMENZ authorization (is it an x402-paying fetch with USDC on Base?)");
  if (r.status >= 400) throw new Error(`OMENZ Authorize error ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export function authorizedFetch(payingFetch, opts = {}) {
  const { maxPriceUsd, expectedPayTo, networks, policy, passportToken, allowReview = false, authorizeUrl = AUTHORIZE_URL, onAuthorization } = opts;
  let spentTodayUsd = 0;
  return async function pay(url, init = {}) {
    const checks = { max_price_usd: maxPriceUsd, expected_pay_to: expectedPayTo, networks, policy, passport_token: passportToken };
    if (policy) checks.spend = { spent_today_usd: spentTodayUsd };
    const res = await authorize(payingFetch, url, init.method || "GET", checks, authorizeUrl);
    if (onAuthorization) await onAuthorization(res);
    if (res.decision === "DECLINE") throw new AuthorizationError("DECLINE", res);
    if (res.decision === "REVIEW" && !allowReview) throw new AuthorizationError("REVIEW", res);
    if (res.decision !== "APPROVE" && res.decision !== "REVIEW") throw new Error("unexpected authorization result");
    const response = await payingFetch(url, init);
    const amt = res.authorization && res.authorization.amount_usd;
    if (response.status < 400 && amt) spentTodayUsd += Number(amt);
    return { response, authorization: res };
  };
}

// ---------------------------------------------------------------- OMENZ Checkout (0.2.0)
// One wrapper = the whole buyer layer around every payment: checkout/open (cheapest working seller if you only
// say what you need, Authorize, seller track record, Shared Budget draw) -> pay only on GO -> checkout/close
// (settlement verified on Base, delivery checked, signed receipt, budget refund if unpaid, seller record).
//
//   import { checkoutFetch } from "omenz-authorize";
//   const buy = checkoutFetch(fetchWithPayment, { maxPriceUsd: 0.05, budgetId, budgetKey, agent: "worker-3" });
//   const { response, data, checkout, receipt } = await buy("https://api.example.com/x402/search", { method: "POST", body: "{...}" });
//   const r2 = await buy({ q: "token price" });   // let OMENZ pick the cheapest seller that answers right now

export const CHECKOUT_BASE = "https://www.omenz.org/api/tollbooth/v1/checkout";

export class CheckoutStopped extends Error {
  constructor(result) {
    super(`OMENZ Checkout ${result.decision}: ${(result.reasons || []).join("; ") || "no reason given"}`);
    this.kind = result.decision;
    this.result = result;
  }
}

async function omenz(payingFetch, url, body, headers = {}) {
  const r = await payingFetch(url, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
  if (r.status === 402) throw new Error("the fetch did not pay OMENZ Checkout (is it an x402-paying fetch with USDC on Base?)");
  if (r.status >= 400) throw new Error(`OMENZ Checkout error ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export function checkoutFetch(payingFetch, opts = {}) {
  const { maxPriceUsd, expectedPayTo, policy, budgetId, budgetKey, agent, jobId, allowReview = false,
          creditId, creditKey, base = CHECKOUT_BASE, onCheckout } = opts;
  const credit = creditId && creditKey ? { "x-credit-id": creditId, "x-credit-key": creditKey } : null;
  const hdr = { ...(budgetKey ? { "x-budget-key": budgetKey } : {}), ...(credit || {}) };
  const path = (side) => `${base}/${side}${credit ? "/credit" : ""}`;
  return async function buy(target, init = {}) {
    const need = typeof target === "string" ? { url: target } : { q: target.q };
    const opened = await omenz(payingFetch, path("open"), {
      ...need, method: (init.method || "GET").toUpperCase(), max_price_usd: maxPriceUsd, expected_pay_to: expectedPayTo,
      policy, budget_id: budgetId, agent, job_id: jobId, purpose: init.purpose }, hdr);
    if (onCheckout) await onCheckout("open", opened);
    if (opened.decision === "STOP" || (opened.decision === "REVIEW" && !allowReview)) throw new CheckoutStopped(opened);
    const { purpose, ...fetchInit } = init;
    let response;
    try {
      response = await payingFetch(opened.pay.url, fetchInit);
    } catch (e) {
      await omenz(payingFetch, path("close"), { checkout_id: opened.checkout_id, not_paid: true }, hdr).catch(() => {});
      throw e;
    }
    let data = null;
    try { data = await response.clone().json(); } catch { try { data = await response.clone().text(); } catch {} }
    const pr = response.headers && (response.headers.get("payment-response") || response.headers.get("x-payment-response"));
    const closeBody = pr ? { checkout_id: opened.checkout_id, payment_response: pr, delivered: data }
                         : { checkout_id: opened.checkout_id, not_paid: response.status !== 200, delivered: data };
    const receipt = await omenz(payingFetch, path("close"), closeBody, hdr);
    if (onCheckout) await onCheckout("close", receipt);
    return { response, data, checkout: opened, receipt };
  };
}

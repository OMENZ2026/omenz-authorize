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

// ---------------------------------------------------------------- OMENZ Transaction Guard / Control Plane (0.3.0)
// One wrapper = every payment your agent makes goes AUTHORITY -> BUDGET -> SELLER -> PRICE -> DUPLICATE CHECK
// (POST /v1/guard) -> pay only on GO -> SETTLED -> DELIVERY VERIFIED -> RECEIPT (checkout/close).
// If the paid request throws (timeout / network), it asks Settlement Rescue before you ever pay again.
//
//   import { guardFetch } from "omenz-authorize";
//   const buy = guardFetch(fetchWithPayment, { payer: myWalletAddress, maxPriceUsd: 0.05, budgetId, budgetKey });
//   const { data, receipt, guard } = await buy("https://api.example.com/x402/price", { purpose: "BTC price" });
//   const r2 = await buy({ need: "token price" });   // OMENZ Market picks the working seller, then guards it

export const OMENZ_V1 = "https://www.omenz.org/api/tollbooth/v1";

export class GuardStopped extends Error {
  constructor(result) {
    super(`OMENZ Guard ${result.decision}: ${(result.reasons || []).join("; ") || "no reason given"}`);
    this.kind = result.decision;
    this.result = result;
  }
}

export function guardFetch(payingFetch, opts = {}) {
  const { payer, maxPriceUsd, budgetId, budgetKey, agent, allowCaution = false, creditId, creditKey, base = OMENZ_V1, onEvent } = opts;
  const credit = creditId && creditKey ? { "x-credit-id": creditId, "x-credit-key": creditKey } : null;
  const hdr = { ...(budgetKey ? { "x-budget-key": budgetKey } : {}), ...(credit || {}) };
  const call = (path, body) => omenz(payingFetch, `${base}/${path}`, body, hdr);
  return async function buy(target, init = {}) {
    const { purpose, quotedPriceUsd, ...fetchInit } = init;
    let g;
    if (typeof target === "string") {
      g = await call(credit ? "guard/credit" : "guard", { url: target, method: (fetchInit.method || "GET").toUpperCase(), payer,
        max_price_usd: maxPriceUsd, quoted_price_usd: quotedPriceUsd, budget_id: budgetId, agent, purpose });
    } else {
      const job = await call("market/job/guarded", { need: target.need || target.q, max_price_usd: maxPriceUsd, payer, budget_id: budgetId, purpose });
      g = { ...(job.guard || {}), market: job };
      if (!job.pick) g = { decision: "STOP", reasons: ["no working provider found"], market: job };
      else if (!g.pay) g.pay = { url: job.pick.url, method: job.pick.method };
    }
    if (onEvent) await onEvent("guard", g);
    if (g.decision === "STOP" || (g.decision === "CAUTION" && !allowCaution)) throw new GuardStopped(g);
    let response;
    try {
      response = await payingFetch(g.pay.url, fetchInit);
    } catch (e) {
      const rescue = await call("settlement/rescue", { payer, pay_to: g.pay.pay_to, amount_usd: g.pay.amount_usd }).catch(() => null);
      if (onEvent) await onEvent("rescue", rescue);
      e.omenzRescue = rescue;
      throw e;
    }
    let data = null;
    try { data = await response.clone().json(); } catch { try { data = await response.clone().text(); } catch {} }
    const pr = response.headers && (response.headers.get("payment-response") || response.headers.get("x-payment-response"));
    const receipt = g.checkout_id ? await call(credit ? "checkout/close/credit" : "checkout/close",
      pr ? { checkout_id: g.checkout_id, payment_response: pr, delivered: data }
         : { checkout_id: g.checkout_id, not_paid: response.status !== 200, delivered: data }) : null;
    if (onEvent) await onEvent("close", receipt);
    return { response, data, guard: g, receipt };
  };
}


export const SEND_CHECK_URL = "https://www.omenz.org/api/tollbooth/v1/send/check";

// OMENZ Send Check ($0.005 over x402) before your agent sends USDC/ETH on Base: SEND / CAUTION / STOP.
// STOP (sanctioned recipient, address poisoning vs your past payees, token-contract recipient, low balance) throws;
// CAUTION throws too unless allowCaution. Never signs or moves money.
export async function sendCheck(payingFetch, { to, amount, asset = "USDC", from, allowCaution = false, url = SEND_CHECK_URL } = {}) {
  const body = { to, asset };
  if (amount !== undefined) body.amount = amount;
  if (from) body.from = from;
  const r = await payingFetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 402) throw new Error("the fetch did not pay OMENZ Send Check (is it an x402-paying fetch with USDC on Base?)");
  if (r.status >= 400) throw new Error(`OMENZ Send Check error ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const res = await r.json();
  if (res.verdict === "STOP" || (res.verdict === "CAUTION" && !allowCaution)) {
    const e = new Error(`OMENZ Send Check ${res.verdict}: ${(res.stop_reasons || res.caution_reasons || []).join("; ")}`);
    e.result = res;
    throw e;
  }
  return res;
}

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

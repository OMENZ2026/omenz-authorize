import json

AUTHORIZE_URL = "https://www.omenz.org/api/tollbooth/v1/authorize"


class AuthorizationDeclined(Exception):
    def __init__(self, result):
        super().__init__("OMENZ Authorize declined: " + "; ".join(result.get("decline_reasons") or ["no reason given"]))
        self.result = result


class AuthorizationNeedsReview(Exception):
    def __init__(self, result):
        super().__init__("OMENZ Authorize wants review: " + "; ".join(result.get("review_reasons") or ["no reason given"]))
        self.result = result


def authorize(session, url, method="GET", authorize_url=AUTHORIZE_URL, timeout=30, **checks):
    """Ask OMENZ Authorize about a payment. `session` pays the $0.001 authorization over x402.
    checks: max_price_usd, expected_pay_to, networks, policy, spend, passport_token, tool."""
    body = {"url": url, "method": method.upper(), **{k: v for k, v in checks.items() if v is not None}}
    r = session.request("POST", authorize_url, json=body, timeout=timeout)
    if r.status_code == 402:
        raise RuntimeError("the session did not pay the OMENZ authorization (is it an x402-paying session with USDC on Base?)")
    if r.status_code >= 400:
        raise RuntimeError(f"OMENZ Authorize error {r.status_code}: {r.text[:300]}")
    return r.json()


class AuthorizedPayer:
    """Wrap an x402-paying session so every paid request is authorized first."""

    def __init__(self, session, max_price_usd=None, expected_pay_to=None, networks=None, policy=None,
                 passport_token=None, allow_review=False, authorize_url=AUTHORIZE_URL, on_authorization=None):
        self.session = session
        self.defaults = {"max_price_usd": max_price_usd, "expected_pay_to": expected_pay_to, "networks": networks,
                         "policy": policy, "passport_token": passport_token}
        self.allow_review = allow_review
        self.authorize_url = authorize_url
        self.on_authorization = on_authorization  # e.g. append to your Job Ledger
        self.spent_today_usd = 0.0

    def request(self, method, url, spend=None, **kwargs):
        checks = {**self.defaults}
        if checks.get("policy") is not None:
            checks["spend"] = {"spent_today_usd": self.spent_today_usd, **(spend or {})}
        res = authorize(self.session, url, method, self.authorize_url, **checks)
        if self.on_authorization:
            self.on_authorization(res)
        d = res.get("decision")
        if d == "DECLINE":
            raise AuthorizationDeclined(res)
        if d == "REVIEW" and not self.allow_review:
            raise AuthorizationNeedsReview(res)
        if d not in ("APPROVE", "REVIEW"):
            raise RuntimeError(f"unexpected authorization result: {json.dumps(res)[:300]}")
        resp = self.session.request(method, url, **kwargs)
        amt = (res.get("authorization") or {}).get("amount_usd")
        if resp.status_code < 400 and amt:
            self.spent_today_usd += float(amt)
        return resp, res

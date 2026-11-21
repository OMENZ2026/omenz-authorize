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


# ---------------------------------------------------------------- OMENZ Checkout (0.2.0)
CHECKOUT_BASE = "https://www.omenz.org/api/tollbooth/v1/checkout"


class CheckoutStopped(Exception):
    def __init__(self, result):
        super().__init__(f"OMENZ Checkout {result.get('decision')}: " + "; ".join(result.get("reasons") or ["no reason given"]))
        self.kind = result.get("decision")
        self.result = result


class CheckoutPayer:
    """The whole OMENZ buyer layer around every payment: checkout/open -> pay only on GO -> checkout/close
    (settlement verified on Base, delivery checked, signed receipt, budget refund if unpaid, seller record).

        buyer = CheckoutPayer(paying_session, max_price_usd=0.05, budget_id="bg_...", budget_key="...", agent="worker-3")
        resp, data, checkout, receipt = buyer.request("GET", "https://api.example.com/x402/price")
        resp, data, checkout, receipt = buyer.request("GET", q="token price")  # OMENZ picks the cheapest working seller
    """

    def __init__(self, session, max_price_usd=None, expected_pay_to=None, policy=None, budget_id=None, budget_key=None,
                 agent=None, job_id=None, allow_review=False, credit_id=None, credit_key=None, base=CHECKOUT_BASE, on_checkout=None):
        self.session = session
        self.defaults = {"max_price_usd": max_price_usd, "expected_pay_to": expected_pay_to, "policy": policy,
                         "budget_id": budget_id, "agent": agent, "job_id": job_id}
        self.credit = {"X-Credit-Id": credit_id, "X-Credit-Key": credit_key} if credit_id and credit_key else None
        self.headers = {**({"X-Budget-Key": budget_key} if budget_key else {}), **(self.credit or {})}
        self.allow_review, self.base, self.on_checkout = allow_review, base, on_checkout

    def _call(self, side, body, timeout=60):
        url = f"{self.base}/{side}" + ("/credit" if self.credit else "")
        r = self.session.request("POST", url, json={k: v for k, v in body.items() if v is not None}, headers=self.headers, timeout=timeout)
        if r.status_code == 402:
            raise RuntimeError("the session did not pay OMENZ Checkout (is it an x402-paying session with USDC on Base?)")
        if r.status_code >= 400:
            raise RuntimeError(f"OMENZ Checkout error {r.status_code}: {r.text[:300]}")
        return r.json()

    def request(self, method="GET", url=None, q=None, purpose=None, **kwargs):
        opened = self._call("open", {**self.defaults, "url": url, "q": None if url else q, "method": method.upper(), "purpose": purpose})
        if self.on_checkout:
            self.on_checkout("open", opened)
        if opened.get("decision") == "STOP" or (opened.get("decision") == "REVIEW" and not self.allow_review):
            raise CheckoutStopped(opened)
        try:
            resp = self.session.request(method, opened["pay"]["url"], **kwargs)
        except Exception:
            try:
                self._call("close", {"checkout_id": opened["checkout_id"], "not_paid": True})
            finally:
                raise
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        pr = resp.headers.get("payment-response") or resp.headers.get("x-payment-response")
        body = {"checkout_id": opened["checkout_id"], "delivered": data}
        body.update({"payment_response": pr} if pr else {"not_paid": resp.status_code != 200})
        receipt = self._call("close", body)
        if self.on_checkout:
            self.on_checkout("close", receipt)
        return resp, data, opened, receipt


# ---------------------------------------------------------------- OMENZ Transaction Guard / Control Plane (0.3.0)
OMENZ_V1 = "https://www.omenz.org/api/tollbooth/v1"


class GuardStopped(Exception):
    def __init__(self, result):
        super().__init__(f"OMENZ Guard {result.get('decision')}: " + "; ".join(result.get("reasons") or ["no reason given"]))
        self.kind = result.get("decision")
        self.result = result


class GuardPayer:
    """Every payment: POST /v1/guard (budget, seller, price, duplicate, economics, delivery) -> pay only on GO ->
    checkout/close (verified settlement + delivery + signed receipt). A paid request that raises triggers Settlement
    Rescue (attached to the exception as .omenz_rescue) so the agent never re-pays blind.

        buyer = GuardPayer(paying_session, payer="0xYourWallet", max_price_usd=0.05, budget_id="bg_...", budget_key="...")
        resp, data, guard, receipt = buyer.request("GET", "https://api.example.com/x402/price", purpose="BTC price")
        resp, data, guard, receipt = buyer.request("GET", need="token price")
    """

    def __init__(self, session, payer=None, max_price_usd=None, budget_id=None, budget_key=None, agent=None,
                 allow_caution=False, credit_id=None, credit_key=None, base=OMENZ_V1, on_event=None):
        self.session, self.payer, self.max_price_usd, self.budget_id, self.agent = session, payer, max_price_usd, budget_id, agent
        self.allow_caution, self.base, self.on_event = allow_caution, base, on_event
        self.credit = bool(credit_id and credit_key)
        self.headers = {**({"X-Budget-Key": budget_key} if budget_key else {}),
                        **({"X-Credit-Id": credit_id, "X-Credit-Key": credit_key} if self.credit else {})}

    def _call(self, path, body, timeout=90):
        r = self.session.request("POST", f"{self.base}/{path}", json={k: v for k, v in body.items() if v is not None},
                                 headers=self.headers, timeout=timeout)
        if r.status_code == 402:
            raise RuntimeError("the session did not pay OMENZ (is it an x402-paying session with USDC on Base?)")
        if r.status_code >= 400:
            raise RuntimeError(f"OMENZ error {r.status_code}: {r.text[:300]}")
        return r.json()

    def _ev(self, kind, x):
        if self.on_event:
            self.on_event(kind, x)

    def request(self, method="GET", url=None, need=None, purpose=None, quoted_price_usd=None, **kwargs):
        if url:
            g = self._call("guard/credit" if self.credit else "guard", {"url": url, "method": method.upper(), "payer": self.payer,
                           "max_price_usd": self.max_price_usd, "quoted_price_usd": quoted_price_usd, "budget_id": self.budget_id,
                           "agent": self.agent, "purpose": purpose})
        else:
            job = self._call("market/job/guarded", {"need": need, "max_price_usd": self.max_price_usd, "payer": self.payer,
                                                    "budget_id": self.budget_id, "purpose": purpose})
            g = dict(job.get("guard") or {})
            g["market"] = job
            if not job.get("pick"):
                g.update({"decision": "STOP", "reasons": ["no working provider found"]})
            elif not g.get("pay"):
                g["pay"] = {"url": job["pick"]["url"], "method": job["pick"].get("method")}
        self._ev("guard", g)
        if g.get("decision") == "STOP" or (g.get("decision") == "CAUTION" and not self.allow_caution):
            raise GuardStopped(g)
        pay = g["pay"]
        try:
            resp = self.session.request(method, pay["url"], **kwargs)
        except Exception as e:
            try:
                e.omenz_rescue = self._call("settlement/rescue", {"payer": self.payer, "pay_to": pay.get("pay_to"), "amount_usd": pay.get("amount_usd")})
                self._ev("rescue", e.omenz_rescue)
            except Exception:
                pass
            raise
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        pr = resp.headers.get("payment-response") or resp.headers.get("x-payment-response")
        receipt = None
        if g.get("checkout_id"):
            body = {"checkout_id": g["checkout_id"], "delivered": data}
            body.update({"payment_response": pr} if pr else {"not_paid": resp.status_code != 200})
            receipt = self._call("checkout/close/credit" if self.credit else "checkout/close", body)
        self._ev("close", receipt)
        return resp, data, g, receipt

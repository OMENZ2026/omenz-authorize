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
    """Ask OMENZ Authorize about a payment. `session` pays the $0.002 authorization over x402.
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


SEND_CHECK_URL = "https://www.omenz.org/api/tollbooth/v1/send/check"


class SendStopped(Exception):
    def __init__(self, result):
        super().__init__("OMENZ Send Check STOP: " + "; ".join(result.get("stop_reasons") or ["no reason given"]))
        self.result = result


def send_check(session, to, amount=None, asset="USDC", sender=None, allow_caution=False, url=SEND_CHECK_URL, timeout=30):
    """Ask OMENZ Send Check before your agent sends USDC/ETH on Base ($0.005 over x402, paid by `session`).
    STOP (sanctioned recipient, address poisoning vs your past payees, token-contract recipient, low balance) raises SendStopped;
    CAUTION raises too unless allow_caution=True. Returns the full verdict. Never signs or moves money."""
    body = {"to": to, "asset": asset, **({"amount": amount} if amount is not None else {}), **({"from": sender} if sender else {})}
    r = session.request("POST", url, json=body, timeout=timeout)
    if r.status_code == 402:
        raise RuntimeError("the session did not pay OMENZ Send Check (is it an x402-paying session with USDC on Base?)")
    if r.status_code >= 400:
        raise RuntimeError(f"OMENZ Send Check error {r.status_code}: {r.text[:300]}")
    res = r.json()
    if res.get("verdict") == "STOP" or (res.get("verdict") == "CAUTION" and not allow_caution):
        raise SendStopped(res)
    return res


TOLLBOOTH = "https://www.omenz.org/api/tollbooth/v1"


def _ok(r, what):
    if r.status_code == 402:
        raise RuntimeError(f"the session did not pay {what} (is it an x402-paying session with USDC on Base?)")
    if r.status_code >= 400:
        raise RuntimeError(f"{what} error {r.status_code}: {r.text[:300]}")
    return r.json()


class Credits:
    """OMENZ prepaid credits: pay once ($1 or $5 over x402), then run Authorize from the balance at 2% of the checked
    payment (min $0.0003) with no per-check settlement. Keep credit_key secret: it's shown once.

        c = Credits.buy(paying_session, dollars=1)        # one x402 payment
        res = c.authorize(plain_session, "https://api.example.com/x402/search", max_price_usd=0.05)
        c.balance(plain_session)
    """

    def __init__(self, credit_id, credit_key, base=TOLLBOOTH):
        self.credit_id, self.credit_key, self.base = credit_id, credit_key, base

    @property
    def _h(self):
        return {"X-Credit-Id": self.credit_id, "X-Credit-Key": self.credit_key}

    @classmethod
    def buy(cls, paying_session, dollars=1, base=TOLLBOOTH, timeout=60):
        if dollars not in (1, 5):
            raise ValueError("dollars must be 1 or 5")
        d = _ok(paying_session.request("POST", f"{base}/credits/buy-{dollars}", json={}, timeout=timeout), "OMENZ credits")
        return cls(d["credit_id"], d["credit_key"], base)

    def balance(self, session, timeout=30):
        return _ok(session.request("GET", f"{self.base}/credits/balance", headers=self._h, timeout=timeout), "OMENZ credits balance")

    def authorize(self, session, url, method="GET", raise_on_decline=True, allow_review=False, timeout=30, **checks):
        res = _ok(session.request("POST", f"{self.base}/authorize/credit", headers=self._h,
                                  json={"url": url, "method": method, **checks}, timeout=timeout), "OMENZ Authorize (credits)")
        if raise_on_decline and res.get("decision") == "DECLINE":
            raise AuthorizationDeclined(res)
        if raise_on_decline and res.get("decision") == "REVIEW" and not allow_review:
            raise AuthorizationNeedsReview(res)
        return res


class Jacket:
    """OMENZ Job Jacket: the minimum working state for one job, encrypted with a key only you hold.
    A worker checks it out at the start of a shift (gets a lease) and checks it back in as a new version.
    Each call is $0.002 over x402, paid by `session`. Never put credentials in a jacket: use reference names.

        j = Jacket.create(session, objective="reconcile November invoices", state={"done": []})
        work = j.checkout(session, worker="agent-7", fields=["done"])
        j.checkin(session, worker="agent-7", state_patch={"done": ["inv-1"]}, checkpoint="inv-1 matched")
    """

    def __init__(self, jacket_id, jacket_key, base=TOLLBOOTH):
        self.jacket_id, self.jacket_key, self.base = jacket_id, jacket_key, base

    @property
    def _h(self):
        return {"X-Jacket-Id": self.jacket_id, "X-Jacket-Key": self.jacket_key}

    @classmethod
    def create(cls, session, objective, base=TOLLBOOTH, timeout=60, **spec):
        d = _ok(session.request("POST", f"{base}/jacket/create", json={"objective": objective, **spec}, timeout=timeout), "OMENZ Job Jacket")
        return cls(d["jacket_id"], d["jacket_key"], base)

    def checkout(self, session, worker, timeout=60, **opts):
        """opts: fields, max_bytes, lease_minutes, take_over, passport_token."""
        return _ok(session.request("POST", f"{self.base}/jacket/checkout", headers=self._h, json={"worker": worker, **opts},
                                   timeout=timeout), "OMENZ Job Jacket checkout")

    def checkin(self, session, worker, ledger_key=None, timeout=60, **update):
        """update: state_patch (merge; None deletes a key) or state (replace), checkpoint, note."""
        h = {**self._h, **({"X-Ledger-Key": ledger_key} if ledger_key else {})}
        return _ok(session.request("POST", f"{self.base}/jacket/checkin", headers=h, json={"worker": worker, **update},
                                   timeout=timeout), "OMENZ Job Jacket checkin")

    def history(self, session, timeout=30):
        return _ok(session.request("GET", f"{self.base}/jacket/history", headers=self._h, timeout=timeout), "OMENZ Job Jacket history")

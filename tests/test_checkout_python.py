import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from omenz_authorize import CheckoutPayer, CheckoutStopped  # noqa: E402


class Resp:
    def __init__(self, code, data, headers=None):
        self.status_code, self._d, self.headers, self.text = code, data, headers or {}, str(data)

    def json(self):
        return self._d


class Session:
    def __init__(self, decision):
        self.decision, self.calls = decision, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if "/checkout/open" in url:
            return Resp(200, {"decision": self.decision, "checkout_id": "ck_1", "reasons": ["r"], "pay": {"url": "https://seller.example/api"}})
        if "/checkout/close" in url:
            return Resp(200, {"verdict": "GOOD_BUY", "sent": kw.get("json")})
        return Resp(200, {"price": 1}, {"payment-response": "eyJ0cmFuc2FjdGlvbiI6IjB4MSJ9"})


ok = 0
s = Session("GO")
resp, data, opened, receipt = CheckoutPayer(s, max_price_usd=0.01, budget_key="k").request("GET", q="token price")
ok += receipt["verdict"] == "GOOD_BUY" and receipt["sent"]["payment_response"] and data["price"] == 1 and len(s.calls) == 3 \
    and s.calls[0][2]["headers"]["X-Budget-Key"] == "k" and "q" in s.calls[0][2]["json"]
s = Session("STOP")
try:
    CheckoutPayer(s).request("GET", "https://seller.example/api")
except CheckoutStopped as e:
    ok += e.kind == "STOP" and len(s.calls) == 1
s = Session("REVIEW")
CheckoutPayer(s, allow_review=True).request("GET", "https://seller.example/api")
ok += len(s.calls) == 3
s = Session("GO")
CheckoutPayer(s, credit_id="c", credit_key="k").request("GET", "https://seller.example/api")
ok += s.calls[0][1].endswith("/checkout/open/credit") and s.calls[2][1].endswith("/checkout/close/credit")
print(ok, "/ 4")
sys.exit(0 if ok == 4 else 1)

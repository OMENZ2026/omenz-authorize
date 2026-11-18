import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from omenz_authorize import GuardPayer, GuardStopped  # noqa: E402


class Resp:
    def __init__(self, code, data, headers=None):
        self.status_code, self._d, self.headers, self.text = code, data, headers or {}, str(data)

    def json(self):
        return self._d


class Session:
    def __init__(self, decision, boom=False):
        self.decision, self.boom, self.calls = decision, boom, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if url.startswith("https://seller") and self.boom:
            raise TimeoutError("seller timeout")
        if url.endswith("/guard") or url.endswith("/guard/credit"):
            return Resp(200, {"decision": self.decision, "reasons": ["r"], "checkout_id": "ck_1",
                              "pay": {"url": "https://seller.example/api", "pay_to": "0xabc", "amount_usd": 0.003}})
        if url.endswith("/market/job/guarded"):
            return Resp(200, {"pick": {"url": "https://seller.example/api", "method": "GET"},
                              "guard": {"decision": self.decision, "checkout_id": "ck_2", "pay": {"url": "https://seller.example/api"}}})
        if "/checkout/close" in url:
            return Resp(200, {"verdict": "GOOD_BUY", "sent": kw.get("json")})
        if url.endswith("/settlement/rescue"):
            return Resp(200, {"verdict": "STILL_UNCERTAIN", "sent": kw.get("json")})
        return Resp(200, {"price": 1}, {"payment-response": "eyJ0cmFuc2FjdGlvbiI6IjB4MSJ9"})


ok = 0
s = Session("GO")
resp, data, g, receipt = GuardPayer(s, payer="0xme", budget_key="k").request("GET", "https://seller.example/api", purpose="p")
ok += bool(receipt["verdict"] == "GOOD_BUY" and data["price"] == 1 and len(s.calls) == 3 and s.calls[0][2]["json"]["payer"] == "0xme" \
    and s.calls[0][2]["headers"]["X-Budget-Key"] == "k" and receipt["sent"]["payment_response"])
s = Session("CAUTION")
try:
    GuardPayer(s).request("GET", "https://seller.example/api")
except GuardStopped as e:
    ok += bool(e.kind == "CAUTION" and len(s.calls) == 1)
s = Session("GO", boom=True)
try:
    GuardPayer(s, payer="0xme").request("GET", "https://seller.example/api")
except TimeoutError as e:
    ok += bool(e.omenz_rescue["verdict"] == "STILL_UNCERTAIN" and e.omenz_rescue["sent"]["pay_to"] == "0xabc")
s = Session("GO")
GuardPayer(s).request("GET", need="token price")
ok += bool(s.calls[0][1].endswith("/market/job/guarded") and len(s.calls) == 3)
s = Session("GO")
GuardPayer(s, credit_id="c", credit_key="k").request("GET", "https://seller.example/api")
ok += bool(s.calls[0][1].endswith("/guard/credit") and s.calls[2][1].endswith("/checkout/close/credit"))
print(ok, "/ 5")
sys.exit(0 if ok == 5 else 1)

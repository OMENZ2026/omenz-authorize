import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from omenz_authorize import AuthorizationDeclined, AuthorizationNeedsReview, AuthorizedPayer  # noqa: E402


class R:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._d, self.text = code, data, text

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, decision, amount=0.01):
        self.decision, self.amount, self.calls = decision, amount, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        if url.endswith("/v1/authorize"):
            return R(200, {"decision": self.decision, "decline_reasons": ["swapped payTo"], "review_reasons": ["unknown payee"],
                           "authorization": {"amount_usd": self.amount, "auth_id": "az_x"}})
        return R(200, {"ok": True})


def run():
    ok = 0
    s = FakeSession("APPROVE")
    p = AuthorizedPayer(s, max_price_usd=0.05, policy={"daily_cap_usd": 1})
    resp, auth = p.request("POST", "https://x.example/api", json={"q": 1})
    ok += bool(resp.json()["ok"] and len(s.calls) == 2 and abs(p.spent_today_usd - 0.01) < 1e-9 and s.calls[0][2]["spend"]["spent_today_usd"] == 0)
    s = FakeSession("DECLINE")
    try:
        AuthorizedPayer(s).request("GET", "https://x.example/api")
    except AuthorizationDeclined as e:
        ok += bool(len(s.calls) == 1 and "swapped" in str(e))
    s = FakeSession("REVIEW")
    try:
        AuthorizedPayer(s).request("GET", "https://x.example/api")
    except AuthorizationNeedsReview:
        ok += len(s.calls) == 1
    s = FakeSession("REVIEW")
    AuthorizedPayer(s, allow_review=True).request("GET", "https://x.example/api")
    ok += len(s.calls) == 2
    print(ok, "/ 4")
    return ok == 4


if __name__ == "__main__":
    sys.exit(0 if run() else 1)

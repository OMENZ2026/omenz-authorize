"""Live test of Jacket helpers against OMENZ AYNE (free playzone: same routes, /ayne prefix, no payment)."""
import sys, requests
sys.path.insert(0, "python")
from omenz_authorize import Jacket, Credits
AYNE = "https://www.omenz.org/api/tollbooth/ayne/v1"
s = requests.Session(); s.headers["User-Agent"] = "omenz-selfcheck"
j = Jacket.create(s, objective="test reconcile", base=AYNE, state={"done": []})
co = j.checkout(s, worker="agent-7")
ci = j.checkin(s, worker="agent-7", state_patch={"done": ["inv-1"]}, checkpoint="inv-1")
h = j.history(s)
print("jacket", j.jacket_id[:8], "checkout keys", sorted(co)[:5], "checkin", ci.get("version"), "history versions", len(h.get("history") or h.get("versions") or []), sorted(h))
c = Credits("cr_none", "bad", base=AYNE)
try:
    c.balance(s); print("credits: unexpected ok")
except RuntimeError as e:
    print("credits bad key ->", str(e)[:80])

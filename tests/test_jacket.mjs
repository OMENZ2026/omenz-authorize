import { Jacket } from "../js/index.js";
const AYNE = "https://www.omenz.org/api/tollbooth/ayne/v1";
const f = (u, o = {}) => fetch(u, { ...o, headers: { ...(o.headers || {}), "User-Agent": "omenz-selfcheck" } });
const j = await Jacket.create(f, "test reconcile js", { state: { done: [] } }, AYNE);
await j.checkout(f, "agent-js");
const ci = await j.checkin(f, "agent-js", { state_patch: { done: ["a"] }, checkpoint: "a" });
console.log("js jacket", j.jacketId.slice(0, 8), "checkin version", ci.version);

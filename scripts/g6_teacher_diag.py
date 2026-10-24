"""G6 diagnostic: offline column planner (teacher) vs official online (computo_b150_l4) on SYNTHETIC flows.
Utilization = mean over CLOSED pallets (the last pallet of each side is excluded on both sides)."""
import json, os, sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
ROOT = os.path.expanduser("~/code/Paletizador/09_Software/paletizado-autopilot-2")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
os.environ["OMP_NUM_THREADS"] = "1"


def offline(task):
    seed, prior = task
    sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
    from palca.learning.synthetic_flows import synthetic_flow
    from palca.study.offline_search import column_search
    boxes = synthetic_flow(seed, prior)
    r = column_search(boxes, width=8, templates=(0, 1, 2), column_gap_mm=0, cpu_seconds=900,
                      tall_box_exemption=True)
    utils = [h["utilization"] for h in r["history"]][:-1]
    return dict(side="offline", seed=seed, prior=prior, util=sum(utils) / len(utils), n=len(utils),
                templates=[h["template"] for h in r["history"]], budget_hit=r["budget_hit"])


def online(task):
    seed, prior = task
    sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
    import numpy as np
    from palca.learning.episode import run_episode
    e = run_episode(seed, prior, "official_b150")
    _, idx = np.unique(e["pallet_id"], return_index=True)
    utils = e["util_final"][idx]   # closed pallets only (open last pallet is censored)
    return dict(side="online", seed=seed, prior=prior, util=float(utils.mean()), n=len(utils))


if __name__ == "__main__":
    os.chdir(ROOT)
    flows = [(s, "generic") for s in range(900000, 900010)] + [(s, "families") for s in range(900000, 900010)]
    tasks = [(offline, f) for f in flows] + [(online, f) for f in flows]
    out = []
    with ProcessPoolExecutor(15, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(fn, f) for fn, f in tasks]
        for fu in futures:
            try:
                out.append(fu.result())
            except Exception as exc:
                out.append(dict(error=repr(exc)[:300]))
    json.dump(out, open("/tmp/g6_diag.json", "w"), indent=1)
    import statistics as st
    for prior in ("generic", "families"):
        off = {r["seed"]: r["util"] for r in out if r.get("side") == "offline" and r["prior"] == prior}
        on = {r["seed"]: r["util"] for r in out if r.get("side") == "online" and r["prior"] == prior}
        common = sorted(set(off) & set(on))
        d = [100 * (off[s] - on[s]) for s in common]
        if d:
            m = st.mean(d); sd = st.stdev(d) if len(d) > 1 else 0
            print(f"{prior}: n={len(d)} offline {100*st.mean(off[s] for s in common):.2f} % | online "
                  f"{100*st.mean(on[s] for s in common):.2f} % | offline-online {m:+.2f} pp (sd {sd:.2f})")
    errs = [r for r in out if "error" in r]
    print("errors:", len(errs), errs[:2])

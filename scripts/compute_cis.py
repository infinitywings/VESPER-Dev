#!/usr/bin/env python3
"""Compute mean +/- std and 95% Wilson confidence intervals for RQ-N2 (and
RQ-N1) from the EXISTING per-trial result.json files (no new experiments).

Addresses MobiCom #2583 reviewers C and E ("few trials / no error bars") and
the advisor's Priority 3, and prepares the clean IoT-J numbers.

Reconnection time is summarized by the MEDIAN (with IQR and a count of
reassociation-timeout tail events), not the mean: 802.11 reassociation has a
heavy right tail (occasional ~11 s stalls when a station misses the reassoc
window and retries on the next scan cycle), so the mean is not a robust
central estimate. The median (~115-137 ms) is the WiFi-realistic figure.

Run from repo root:  python3 scripts/compute_cis.py
"""
import json, math, glob, os, statistics

Z = 1.96  # 95%
TIMEOUT_MS = 5000  # reassociation attempts >= this are tail/timeout events


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = (Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / denom
    return (100 * p, 100 * (center - half), 100 * (center + half))


def newcombe_diff(k1, n1, k2, n2):
    """95% CI for p1 - p2 (Newcombe method 10)."""
    p1, l1, u1 = [x / 100 for x in wilson(k1, n1)]
    p2, l2, u2 = [x / 100 for x in wilson(k2, n2)]
    d = p1 - p2
    L = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    U = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (100 * d, 100 * L, 100 * U)


def msd(xs):
    if not xs:
        return (0.0, 0.0)
    return (statistics.mean(xs), statistics.stdev(xs) if len(xs) > 1 else 0.0)


def med_iqr(xs):
    """Return (median, q1, q3, n_tail) — robust summary for reconnection."""
    if not xs:
        return (None, None, None, 0)
    s = sorted(xs)
    med = statistics.median(s)
    if len(s) >= 4:
        q1, q3 = statistics.quantiles(s, n=4)[0], statistics.quantiles(s, n=4)[2]
    else:
        q1, q3 = s[0], s[-1]
    n_tail = sum(1 for v in s if v >= TIMEOUT_MS)
    return (med, q1, q3, n_tail)


def load_config(cfg_dir):
    trials = []
    for tj in sorted(glob.glob(os.path.join(cfg_dir, "trial_*", "result.json"))):
        trials.append(json.load(open(tj)))
    return trials


def rqn2(base):
    print(f"\n{'=' * 92}\nRQ-N2 hardening tradeoffs  ({base})\n{'=' * 92}")
    print(f"{'Cfg':<3}{'Name':<20}{'Attack% [95% Wilson CI]':<28}"
          f"{'Thrpt Mbps (m±sd)':<20}{'Recon ms median [IQR] (#tail)'}")
    agg = {}
    for cfg_dir in sorted(glob.glob(os.path.join(base, "config_*")),
                          key=lambda p: int(p.split("config_")[-1])):
        idx = int(cfg_dir.split("config_")[-1])
        trials = load_config(cfg_dir)
        if not trials:
            continue
        k = sum(t["total_successful"] for t in trials)
        n = sum(t["total_attacks"] for t in trials)
        rate, lo, hi = wilson(k, n)
        thr_m, thr_s = msd([t["throughput_mbps"] for t in trials
                            if t.get("throughput_mbps") is not None])
        recs = [t["reconnection_ms"] for t in trials
                if t.get("reconnection_ms") is not None]
        med, q1, q3, ntail = med_iqr(recs)
        name = trials[0].get("config_name") or trials[0].get("name") or f"cfg{idx}"
        agg[idx] = (k, n, rate)
        rec_str = (f"{med:.1f} [{q1:.0f},{q3:.0f}] ({ntail})"
                   if med is not None else "n/a")
        print(f"{idx:<3}{name:<20}{f'{rate:.1f}  [{lo:.1f}, {hi:.1f}]':<28}"
              f"{f'{thr_m:.2f} ± {thr_s:.2f}':<20}{rec_str}")
    # Headline: baseline (0) vs fully hardened (7)
    if 0 in agg and 7 in agg:
        k0, n0, r0 = agg[0]
        k7, n7, r7 = agg[7]
        d, L, U = newcombe_diff(k0, n0, k7, n7)
        print(f"\nHeadline reduction (cfg0 baseline {r0:.1f}%  ->  cfg7 hardened {r7:.1f}%):")
        print(f"  Delta = {d:.1f} pp   95% CI [{L:.1f}, {U:.1f}] pp   "
              f"excludes 0: {L > 0 or U < 0}   (n={n0}+{n7} Bernoulli attack-trials)")


def _rtt_mean(t):
    vs = [s["mean_ms"] for s in t.get("icmp_rtt", {}).values()
          if isinstance(s, dict) and "mean_ms" in s]
    return statistics.mean(vs) if vs else None


def rqn1(path):
    """RQ-N1 bridge vs 802.11 from rqn1_full_results.json (nested schema)."""
    if not os.path.exists(path):
        print(f"\n[RQ-N1] {path} not found — skipping")
        return
    d = json.load(open(path))
    bt, wt = d.get("bridge_trials", []), d.get("wifi_trials", [])
    print(f"\n{'=' * 92}\nRQ-N1 bridge vs 802.11  ({path})\n"
          f"trials={d.get('num_trials')}  wmediumd={d.get('wmediumd_enabled')} "
          f"scenario={d.get('wmediumd_scenario')}\n{'=' * 92}")

    def col(trials, f):
        xs = [f(t) for t in trials]
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        m, s = msd(xs)
        ci = Z * s / math.sqrt(len(xs)) if len(xs) > 1 else 0.0
        return (m, ci, len(xs))

    def row(label, f, fmt="{:.3f}"):
        b, w = col(bt, f), col(wt, f)
        bs = (f"{fmt.format(b[0])} ± {fmt.format(b[1])} (n={b[2]})") if b else "n/a"
        ws = (f"{fmt.format(w[0])} ± {fmt.format(w[1])} (n={w[2]})") if w else "n/a"
        print(f"  {label:<26} bridge {bs:<30} | wifi {ws}")

    row("ICMP RTT (ms)", _rtt_mean)
    row("TCP throughput (Mbps)", lambda t: t.get("throughput", {}).get("tcp_mbps"), "{:.2f}")
    row("Reconnection (ms)", lambda t: (t.get("reconnection") or {}).get("reconnection_ms"), "{:.1f}")
    row("Firmware attack succ %", lambda t: (t.get("firmware_attacks") or {}).get("rate"), "{:.1f}")
    row("Network attack succ %", lambda t: (t.get("network_attacks") or {}).get("rate"), "{:.1f}")
    row("WiFi attack succ %", lambda t: (t.get("wifi_attacks") or {}).get("rate"), "{:.1f}")

    # Data-integrity guard: WiFi perf metrics should have n≈num_trials, not n=1.
    wperf = col(wt, lambda t: t.get("throughput", {}).get("tcp_mbps"))
    valid = [x for x in (t.get("throughput", {}).get("tcp_mbps") for t in wt)
             if x is not None and x > 0]
    print(f"\n  [integrity] WiFi throughput valid (>0) trials: {len(valid)}/{len(wt)}"
          + ("  <-- OK" if len(valid) >= max(2, len(wt) - 2)
             else "  <-- WARN: perf collapsed after trial 1 (single-setup bug)"))


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    # Clean IoT-J data first; fall back to older run sets.
    rqn2_bases = [
        "results/iotj_wmediumd/iotj/rqn2",
        "results/wmediumd_real/rqn2_wmediumd",
        "results/rqn2_real",
    ]
    for base in rqn2_bases:
        if glob.glob(os.path.join(base, "config_*")):
            rqn2(base)
            break
    rqn1_paths = [
        "results/iotj_v2/rqn1/rqn1_full_results.json",       # fixed re-run
        "results/iotj_wmediumd/iotj/rqn1/rqn1_full_results.json",
        "results/rqn1_real/rqn1_full_results.json",
    ]
    for p in rqn1_paths:
        if os.path.exists(p):
            rqn1(p)
            break

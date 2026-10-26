"""Deterministic advisory budgets for recorded Ask interactions."""

def budget_result(row, seconds=60.0, usd=0.10):
    """Inclusive advisory budgets; incomplete metering cannot earn a cost pass."""
    import math
    if not all(math.isfinite(v) and v > 0 for v in (seconds, usd)):
        raise ValueError("Budgets must be finite positive numbers")
    elapsed, cost = row.get("elapsed_s"), row.get("cost_usd")
    latency = "unknown" if elapsed is None else "pass" if elapsed <= seconds else "over budget"
    complete = row.get("model_requests") is not None and row.get("model_requests") == row.get("metered_calls")
    price = ("over budget" if cost is not None and cost > usd else
             "pass" if complete and cost is not None else "unknown")
    overall = "over budget" if "over budget" in (latency, price) else "unknown" if "unknown" in (latency, price) else "pass"
    return dict(latency=latency, cost=price, overall=overall)

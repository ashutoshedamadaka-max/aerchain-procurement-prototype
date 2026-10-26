"""What the analyst model is shown: money, rates and percentages in tool results, formatted once, at the tool boundary.

The tools compute in full precision and their own tests read those numbers directly. Only the copy handed to the model is formatted, so no answer can quote a
raw float like 18712301.1404928. One convention per kind of value:
  money totals   whole rupees as a string with Indian grouping        "₹1,87,12,301"  (negative: "−₹4,80,827")
  rupee rates    2 decimals as a string                               "₹6.73"          (per piece / per kg after conversion)
  as-quoted      the vendor's own number, up to 3 decimals, a string  "0.076", "31.93" (USD rates are quoted to 3 decimals; never rounded to 2)
  percentages    at most 2 decimals, a string                         "0.26%", "26.17%"
  anything else  a float with more than 2 decimals is rounded to 2 (or kept exactly, as a string, if it is a 3-decimal quoted figure); ints and text are untouched
"""
import math

MONEY = {"naive_total", "repriced_total", "repricing_effect", "earned_discount", "unearned_discount", "baseline_naive", "baseline_repriced", "known_exposure_inr",
         "exposure_inr", "effect_inr", "unearned_inr", "value_at_risk_inr", "actual"}
MONEY_UNDER = {"saving": {"naive", "repriced"}, "per_vendor": {"total"}, "alternatives": {"repriced_total", "naive_total"}}
RATES = {"inr_per_piece", "landed_inr_per_piece", "adjusted_inr_per_piece", "net_inr_pc", "impact_if_changed_1pct_inr_pc", "inr_per_kg"}
QUOTED = {"stated_value", "orig_currency_per_piece"}
QUOTED_UNDER = {"record": {"value"}, "derivation": {"value"}}
PCT = ("_pct",)
FRACTION = {"share_of_award"}


def indian(value, decimals=0):
    """348000 -> '3,48,000'; 40200000 -> '4,02,00,000'."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return ("-" if value < 0 and float(text) != 0 else "") + whole + ("." + frac if frac else "")


def rupees(value):
    return ("−" if round(value) < 0 else "") + "₹" + indian(abs(value), 0)


def rate(value):
    return ("−" if round(value, 2) < 0 else "") + "₹" + indian(abs(value), 2)


def quoted(value):
    """The number as a vendor states it: 3 decimals, dropping one trailing zero (0.076 stays; 31.930 -> 31.93; 38.0 -> 38.00)."""
    text = f"{value:.3f}"
    return text[:-1] if text.endswith("0") else text


def percent(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    if float(text) == 0:
        return "0%"
    return f"{'−' if text.startswith('-') else ''}{text.lstrip('-')}%"


def decimals(x):
    """Decimal digits of a float as written (99 for float noise)."""
    t = repr(x)
    return 99 if "e" in t or "E" in t else (len(t.split(".")[1]) if "." in t else 0)


def _under(table, key, path):
    return any(key in table.get(ancestor, ()) for ancestor in path[:-1])


def _number(x, key, path):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or (isinstance(x, float) and not math.isfinite(x)):
        return x
    if key in MONEY or _under(MONEY_UNDER, key, path):
        return rupees(x)
    if key in RATES:
        return rate(x)
    if key in QUOTED or _under(QUOTED_UNDER, key, path):
        return quoted(x)
    if key and key.endswith(PCT):
        return percent(x)
    if key in FRACTION or (key == "share" and "per_vendor" in path):
        return percent(x * 100)
    if isinstance(x, float) and decimals(x) > 2:
        return quoted(x) if abs(x * 1000 - round(x * 1000)) < 1e-6 else round(x, 2)
    return x


def for_model(obj, key=None, path=(), sibling_unit=None):
    """A copy of a tool result with every money, rate and percentage formatted as described above."""
    if isinstance(obj, dict):
        unit = obj.get("unit")
        return {k: for_model(v, k, path + (k,), unit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [for_model(v, key, path + (key,) if key else path) for v in obj]
    if key == "value" and sibling_unit == "INR":                 # a rupee total quoted by a vendor (e.g. a line total)
        return _number(obj, "known_exposure_inr", path)
    return _number(obj, key, path)

"""Display formatting only: Indian digit grouping and fixed decimals. Nothing here changes a stored or computed value."""


def indian(value, decimals=0):
    """348000 -> '3,48,000'; 40200000 -> '4,02,00,000'; 1234.5 with 2 decimals -> '1,234.50'."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return ("-" if value < 0 and float(text) != 0 else "") + whole + ("." + frac if frac else "")


def price(value, currency):
    """A quoted unit price: USD as vendors quote it (3 decimals), everything else per piece (2 decimals)."""
    return f"{currency or ''} {indian(value, 3 if currency == 'USD' else 2)}".strip()


def total(value, currency):
    """A total or exposure in currency units: no decimals."""
    return f"{currency or ''} {indian(value, 0)}".strip()


def field_value(value, currency, field_name):
    """An extracted field value by what it is: a rate, a line total, or a spec figure such as GSM."""
    if field_name == "unit_price":
        return price(value, currency)
    if field_name == "line_total":
        return total(value, currency)
    return indian(value, 0 if float(value).is_integer() else 2)


def pct(value):
    """A percentage to one decimal with a true minus sign: -1.26 -> '−1.3%'."""
    return f"{'−' if round(value, 1) < 0 else ''}{abs(value):.1f}%"


def saving(rupees, pct_value):
    """A saving without a double negative: 'Saves ₹X (Y%)' when positive, 'Worse by ₹X (−Y%)' when negative."""
    body = f"₹{indian(abs(rupees), 0)} ({pct(pct_value)})"
    return f"Saves {body}" if rupees >= 0 else f"Worse by {body}"

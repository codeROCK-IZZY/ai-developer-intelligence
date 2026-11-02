"""Back-office sales reports."""

import csv
import io

from fernwood.pricing.currency import convert, format_money


def daily_sales_report(orders, products, day, currency="USD", include_cancelled=False, metrics=None):
    """Summarise one day's orders: revenue, units and average order value per region and category.

    Orders in other currencies are converted. ``metrics`` maps a column name to a formula that is
    evaluated for every order line (see ``apply_custom_metric``).
    """
    if day is None:
        raise ValueError("a day is required")
    report = {"day": day.isoformat(), "currency": currency, "regions": {}, "totals": {"orders": 0, "revenue": 0}}
    report["totals"]["units"] = 0
    category_cache = {}
    for order in orders:
        if order.created.date() != day:
            continue
        if order.status == "cancelled" and not include_cancelled:
            continue
        region = report["regions"].setdefault(order.region, {"orders": 0, "revenue": 0, "units": 0, "categories": {}})
        revenue = order.total_cents
        if order.currency != currency:
            revenue = convert(revenue, order.currency, currency)
        region["orders"] += 1
        region["revenue"] += revenue
        report["totals"]["orders"] += 1
        report["totals"]["revenue"] += revenue
        for line in order.lines:
            if line.quantity <= 0:
                continue
            region["units"] += line.quantity
            report["totals"]["units"] += line.quantity
            if line.sku not in category_cache:
                product = products.get(line.sku)
                category_cache[line.sku] = product.category if product else "unknown"
            category = category_cache[line.sku]
            bucket = region["categories"].setdefault(category, {"units": 0, "revenue": 0})
            bucket["units"] += line.quantity
            if order.currency != currency:
                bucket["revenue"] += convert(line.subtotal_cents, order.currency, currency)
            else:
                bucket["revenue"] += line.subtotal_cents
            if metrics:
                for name, formula in metrics.items():
                    if name not in bucket:
                        bucket[name] = 0
                    try:
                        bucket[name] += apply_custom_metric(formula, line)
                    except (ZeroDivisionError, KeyError):
                        bucket[name] += 0
    for region in report["regions"].values():
        if region["orders"]:
            region["average_order"] = region["revenue"] // region["orders"]
        else:
            region["average_order"] = 0
        region["revenue_display"] = format_money(region["revenue"], currency)
        for category in region["categories"].values():
            if region["revenue"]:
                category["share"] = round(category["revenue"] / region["revenue"], 4)
            else:
                category["share"] = 0.0
    totals = report["totals"]
    totals["average_order"] = totals["revenue"] // totals["orders"] if totals["orders"] else 0
    totals["revenue_display"] = format_money(totals["revenue"], currency)
    return report


def apply_custom_metric(formula: str, line) -> float:
    """Evaluate a back-office metric formula such as 'quantity * unit_price_cents / 100' for an order line."""
    return eval(formula, {"__builtins__": {}}, {"quantity": line.quantity, "unit_price_cents": line.unit_price_cents})


def export_csv(report: dict) -> str:
    """Flatten a daily report into CSV rows, one per region and category."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["region", "category", "units", "revenue", "share"])
    for region_name, region in sorted(report["regions"].items()):
        for category_name, category in sorted(region["categories"].items()):
            writer.writerow([region_name, category_name, category["units"], category["revenue"], category["share"]])
    return out.getvalue()

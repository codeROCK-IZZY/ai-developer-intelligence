"""Back-office command line.

    python -m fernwood.cli report 2026-11-01 [--csv]
    python -m fernwood.cli notify +15551234567 "Your parcel is on its way"
"""

import argparse
import json
import os
from datetime import date

from fernwood.dependencies import get_orders, get_products
from fernwood.notifications.provider import MessagingClient
from fernwood.notifications.sms import send_sms
from fernwood.reports.sales import daily_sales_report, export_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fernwood")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="daily sales report")
    report.add_argument("day", type=date.fromisoformat)
    report.add_argument("--csv", action="store_true")
    notify = sub.add_parser("notify", help="send an SMS to a customer")
    notify.add_argument("phone")
    notify.add_argument("text")
    args = parser.parse_args(argv)
    if args.command == "report":
        orders = get_orders().list_for_day(args.day)
        result = daily_sales_report(orders, get_products(), args.day)
        print(export_csv(result) if args.csv else json.dumps(result, indent=2))
    else:
        client = MessagingClient(os.environ["FERNWOOD_SMS_URL"], os.environ["FERNWOOD_SMS_TOKEN"])
        return 0 if send_sms(client, args.phone, args.text) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

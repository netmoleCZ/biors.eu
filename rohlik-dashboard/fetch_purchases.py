#!/usr/bin/env python3
"""Fetch your Rohlik.cz purchase history into local CSV files.

Rohlik has no public API, so this talks to the same private endpoints their
own web app uses, authenticated with your browser session cookie. See
README.md for how to grab that cookie.

Usage:
    python fetch_purchases.py [--out data]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://www.rohlik.cz"
ORDERS_ENDPOINT = f"{BASE_URL}/api/v3/orders/delivered"
ORDER_DETAIL_ENDPOINT = f"{BASE_URL}/api/v3/orders/{{order_id}}"

REQUEST_DELAY_S = 0.2  # be gentle with Rohlik's servers


def load_cookie() -> str:
    load_dotenv()
    cookie = os.environ.get("ROHLIK_COOKIE")
    if not cookie:
        sys.exit(
            "ROHLIK_COOKIE is not set.\n"
            "Copy your rohlik.cz session cookie into a .env file - see .env.example."
        )
    return cookie


def make_session(cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (personal-purchase-dashboard/1.0)",
            "Accept": "application/json",
        }
    )
    return session


def first(d: dict, *keys, default=None):
    for key in keys:
        value = d.get(key)
        if value is not None:
            return value
    return default


def fetch_order_list(session: requests.Session, page_size: int = 50) -> list[dict]:
    """Page through the delivered-orders endpoint until it stops returning results."""
    orders: list[dict] = []
    offset = 0
    while True:
        resp = session.get(
            ORDERS_ENDPOINT, params={"limit": page_size, "offset": offset}, timeout=30
        )
        if resp.status_code in (401, 403):
            sys.exit(
                f"Rohlik rejected the request ({resp.status_code}). "
                "Your session cookie has probably expired - grab a fresh one."
            )
        resp.raise_for_status()
        payload = resp.json()
        batch = (
            payload
            if isinstance(payload, list)
            else payload.get("orders") or payload.get("data") or []
        )
        if not batch:
            break
        orders.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(REQUEST_DELAY_S)
    return orders


def fetch_order_detail(session: requests.Session, order_id: str) -> dict:
    resp = session.get(ORDER_DETAIL_ENDPOINT.format(order_id=order_id), timeout=30)
    resp.raise_for_status()
    return resp.json()


def normalize(raw_orders: list[dict], session: requests.Session) -> tuple[list[dict], list[dict]]:
    order_rows: list[dict] = []
    item_rows: list[dict] = []

    for i, order in enumerate(raw_orders, start=1):
        order_id = first(order, "id", "orderId", "orderNumber")
        print(f"  [{i}/{len(raw_orders)}] order {order_id}")

        detail: dict = {}
        if order_id is not None:
            try:
                detail = fetch_order_detail(session, order_id)
            except requests.HTTPError as exc:
                print(f"    could not fetch detail: {exc}")
            time.sleep(REQUEST_DELAY_S)

        date = first(order, "deliveredAt", "createdAt", "orderTime") or first(
            detail, "deliveredAt", "createdAt"
        )
        price_composition = order.get("priceComposition") or detail.get("priceComposition") or {}
        total_price = (
            first(order, "totalPrice", "price")
            or first(detail, "totalPrice", "price")
            or price_composition.get("total")
        )
        products = detail.get("products") or detail.get("items") or []

        order_rows.append(
            {
                "order_id": order_id,
                "date": date,
                "total_price": total_price,
                "items_count": first(order, "itemsCount", default=len(products)),
            }
        )

        for product in products:
            item_rows.append(
                {
                    "order_id": order_id,
                    "date": date,
                    "product_name": first(product, "productName", "name", default="Unknown"),
                    "brand": product.get("brand"),
                    "quantity": first(product, "quantity", default=1),
                    "unit_price": product.get("price"),
                    "total_price": first(product, "totalPrice", "price"),
                }
            )

    return order_rows, item_rows


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        print(f"  nothing to write for {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {len(rows)} rows to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data", help="Output directory for CSVs (default: data/)")
    args = parser.parse_args()

    session = make_session(load_cookie())

    print("Fetching delivered order list...")
    raw_orders = fetch_order_list(session)
    print(f"Found {len(raw_orders)} delivered orders. Fetching line items...")

    order_rows, item_rows = normalize(raw_orders, session)

    out_dir = Path(args.out)
    write_csv(order_rows, out_dir / "orders.csv")
    write_csv(item_rows, out_dir / "items.csv")

    with (out_dir / "orders_raw.json").open("w", encoding="utf-8") as f:
        json.dump(raw_orders, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Run 'streamlit run dashboard.py' to view the dashboard.")


if __name__ == "__main__":
    main()

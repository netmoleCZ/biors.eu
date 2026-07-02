# Rohlik purchases dashboard

Fetches your Rohlik.cz order history and visualizes it: spend over time, top
products, top brands, and order frequency.

Rohlik has no public/official API, so `fetch_purchases.py` talks to the same
private endpoints their web app uses (`/api/v3/orders/delivered` and
`/api/v3/orders/{id}`), authenticated with your own browser session cookie.
This is a reverse-engineered integration for personal use only, and may break
if Rohlik changes their API.

## Setup

```bash
cd rohlik-dashboard
pip install -r requirements.txt
```

Get your session cookie:

1. Log into https://www.rohlik.cz in your browser.
2. Open DevTools (F12) → Network tab, reload the page.
3. Click any request going to `www.rohlik.cz`.
4. Under "Request Headers", copy the full value of the `Cookie` header.
5. `cp .env.example .env` and paste it in as `ROHLIK_COOKIE`.

The cookie is your login session — treat it like a password. It's only ever
sent to `www.rohlik.cz`, and `.env` / fetched data are already excluded via
`.gitignore` so they won't get committed.

## Usage

```bash
python fetch_purchases.py       # writes data/orders.csv, data/items.csv
streamlit run dashboard.py      # opens the dashboard in your browser
```

`fetch_purchases.py` re-fetches your full delivered-order history each run
(there's no incremental sync). Cookies expire — if you get a 401/403, grab a
fresh one and update `.env`.

Until you've fetched your real data, `dashboard.py` shows bundled sample data
so you can see what it looks like.

## Data layout

- `data/orders.csv` — one row per order: `order_id, date, total_price, items_count`
- `data/items.csv` — one row per line item: `order_id, date, product_name, brand, quantity, unit_price, total_price`
- `data/orders_raw.json` — the raw API response, in case you want other fields later

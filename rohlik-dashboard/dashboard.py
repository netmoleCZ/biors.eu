#!/usr/bin/env python3
"""Streamlit dashboard for your Rohlik.cz purchase history.

Reads data/orders.csv + data/items.csv (produced by fetch_purchases.py).
Falls back to the bundled sample_data/ so the dashboard works out of the box
before you've fetched your real data.

Run with:
    streamlit run dashboard.py
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --- palette (validated categorical + chart-chrome tokens) -----------------
CATEGORICAL = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
BLUE = CATEGORICAL[0]
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
MUTED = "#898781"
INK = "#0b0b0b"
SURFACE = "#fcfcfb"

BASE_LAYOUT = dict(
    template="plotly_white",
    paper_bgcolor=SURFACE,
    plot_bgcolor=SURFACE,
    font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=INK),
    margin=dict(l=10, r=10, t=40, b=10),
)


def apply_axes(fig, x_title=None, y_title=None):
    fig.update_xaxes(title=x_title, showgrid=False, showline=True, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(title=y_title, showgrid=True, gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED))
    return fig


DATA_DIR = Path(__file__).parent / "data"
SAMPLE_DIR = Path(__file__).parent / "sample_data"


@st.cache_data
def load_data():
    source = DATA_DIR if (DATA_DIR / "orders.csv").exists() else SAMPLE_DIR
    orders = pd.read_csv(source / "orders.csv", parse_dates=["date"])
    items = pd.read_csv(source / "items.csv", parse_dates=["date"])
    orders = orders.dropna(subset=["date"]).sort_values("date")
    items = items.dropna(subset=["date"]).sort_values("date")
    return orders, items, source is SAMPLE_DIR


st.set_page_config(page_title="Rohlik purchases", page_icon="\U0001F6D2", layout="wide")

orders, items, using_sample = load_data()

if using_sample:
    st.info(
        "Showing **sample data** — run `python fetch_purchases.py` with your session "
        "cookie set up (see README.md) to see your real Rohlik purchase history."
    )

st.title("Rohlik purchases")

# --- filters -----------------------------------------------------------
min_date, max_date = orders["date"].min().date(), orders["date"].max().date()
date_range = st.slider(
    "Date range", min_value=min_date, max_value=max_date, value=(min_date, max_date)
)
mask = (orders["date"].dt.date >= date_range[0]) & (orders["date"].dt.date <= date_range[1])
orders_f = orders[mask]
item_mask = (items["date"].dt.date >= date_range[0]) & (items["date"].dt.date <= date_range[1])
items_f = items[item_mask]

if orders_f.empty:
    st.warning("No orders in this date range.")
    st.stop()

# --- KPI tiles -----------------------------------------------------------
total_spent = orders_f["total_price"].sum()
order_count = len(orders_f)
avg_order = total_spent / order_count if order_count else 0

col1, col2, col3 = st.columns(3)
col1.metric("Total spent", f"{total_spent:,.0f} Kč")
col2.metric("Orders", f"{order_count}")
col3.metric("Avg. order value", f"{avg_order:,.0f} Kč")

st.divider()

# --- spend over time (monthly) -----------------------------------------
monthly = (
    orders_f.set_index("date")["total_price"]
    .resample("MS")
    .sum()
    .reset_index()
)
monthly["month"] = monthly["date"].dt.strftime("%Y-%m")

fig_spend = go.Figure(
    go.Bar(
        x=monthly["month"],
        y=monthly["total_price"],
        marker=dict(color=BLUE),
        hovertemplate="%{x}<br>%{y:,.0f} Kč<extra></extra>",
    )
)
fig_spend.update_layout(**BASE_LAYOUT, title="Spend per month", height=360, showlegend=False)
apply_axes(fig_spend, y_title="Kč")
st.plotly_chart(fig_spend, use_container_width=True)

left, right = st.columns(2)

# --- top products by spend ----------------------------------------------
with left:
    top_products = (
        items_f.groupby("product_name")["total_price"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .sort_values()
    )
    fig_products = go.Figure(
        go.Bar(
            x=top_products.values,
            y=top_products.index,
            orientation="h",
            marker=dict(color=BLUE),
            hovertemplate="%{y}<br>%{x:,.0f} Kč<extra></extra>",
        )
    )
    fig_products.update_layout(**BASE_LAYOUT, title="Top 10 products by spend", height=420, showlegend=False)
    apply_axes(fig_products, x_title="Kč")
    st.plotly_chart(fig_products, use_container_width=True)

# --- top brands by spend (categorical, fold into "Other" beyond 8) ------
with right:
    by_brand = (
        items_f.dropna(subset=["brand"])
        .groupby("brand")["total_price"]
        .sum()
        .sort_values(ascending=False)
    )
    if len(by_brand) > 8:
        top8 = by_brand.head(8)
        other = pd.Series({"Other": by_brand.iloc[8:].sum()})
        by_brand = pd.concat([top8, other])
        colors_by_name = dict(zip(top8.index, CATEGORICAL[:8]))
        colors_by_name["Other"] = MUTED
    else:
        colors_by_name = dict(zip(by_brand.index, CATEGORICAL[: len(by_brand)]))

    by_brand = by_brand.sort_values()  # ascending so the biggest bar plots on top
    colors = [colors_by_name[name] for name in by_brand.index]

    fig_brands = go.Figure(
        go.Bar(
            x=by_brand.values,
            y=by_brand.index,
            orientation="h",
            marker=dict(color=colors),
            hovertemplate="%{y}<br>%{x:,.0f} Kč<extra></extra>",
        )
    )
    fig_brands.update_layout(**BASE_LAYOUT, title="Spend by brand", height=420, showlegend=False)
    apply_axes(fig_brands, x_title="Kč")
    st.plotly_chart(fig_brands, use_container_width=True)

# --- order frequency by weekday -----------------------------------------
weekday_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
by_weekday = orders_f["date"].dt.day_name().str[:3].value_counts().reindex(weekday_order).fillna(0)
fig_weekday = go.Figure(
    go.Bar(
        x=by_weekday.index,
        y=by_weekday.values,
        marker=dict(color=BLUE),
        hovertemplate="%{x}<br>%{y:.0f} orders<extra></extra>",
    )
)
fig_weekday.update_layout(**BASE_LAYOUT, title="Orders by day of week", height=320, showlegend=False)
apply_axes(fig_weekday, y_title="Orders")
st.plotly_chart(fig_weekday, use_container_width=True)

# --- raw table view (accessibility: table alongside every chart) --------
with st.expander("Orders table"):
    st.dataframe(
        orders_f.sort_values("date", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

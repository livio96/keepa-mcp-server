"""Keepa MCP server — Streamable HTTP transport, deployable to Render.

Single env var required: KEEPA_API
MCP endpoint: <host>/mcp/
Health check : <host>/health
"""
from __future__ import annotations

import os
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from formatters import build_summary_table, format_product, format_search_item
from keepa_client import KeepaClient, KeepaError

API_KEY = os.environ.get("KEEPA_API")
if not API_KEY:
    raise RuntimeError(
        "KEEPA_API environment variable is required (set it in Render dashboard)."
    )

mcp = FastMCP("Keepa")
client = KeepaClient(API_KEY)


@mcp.tool
async def lookup_product(identifier: str, stats_days: int = 365) -> dict:
    """Look up an Amazon US product by ASIN or UPC/EAN code and return a
    Keepa-style price-history summary plus a markdown table ready to display.

    Returns identity (title, brand, category, manufacturer, model), prices for
    Buy Box / Amazon / 3rd-party New / Used / FBA (current, avg-30/90/180/365,
    all-time min & max with timestamps, interval min & max with timestamps,
    out-of-stock % over 30/90/365 days), sales-rank stats, monthly-sold
    estimate, Buy Box ownership info, offer counts, rating, review count, and
    a `summary_table` markdown string suitable for direct rendering.

    Args:
        identifier: 10-character ASIN (e.g. "B08N5WRWNW") or UPC/EAN digits.
        stats_days: Window in days for interval extremes & out-of-stock %.
            Default 365 so the response includes both all-time and 365-day
            extremes (matches Keepa's website table).
    """
    ident = identifier.strip()
    is_asin = len(ident) == 10 and ident[0].isalpha() and ident.isalnum()
    try:
        if is_asin:
            data = await client.product(asin=ident, stats_days=stats_days)
        else:
            digits = "".join(c for c in ident if c.isdigit())
            if not digits:
                return {"error": f"'{identifier}' is not a valid ASIN or UPC/EAN."}
            data = await client.product(code=digits, stats_days=stats_days)
    except KeepaError as e:
        return {"error": str(e)}

    products = data.get("products") or []
    if not products:
        return {"error": f"No product found for '{identifier}'."}
    summary = format_product(products[0])
    summary["tokens_left"] = data.get("tokensLeft")
    summary["refill_in_ms"] = data.get("refillIn")
    summary["summary_table"] = build_summary_table(summary)
    return summary


@mcp.tool
async def search_products(keyword: str, page: int = 0) -> dict:
    """Keyword search across Amazon US. Returns ASINs (with title, price,
    sales rank, monthly_sold when available) ranked by Keepa relevance.

    Args:
        keyword: Search term (e.g. "wireless earbuds").
        page: Zero-indexed result page (~40 ASINs per page).
    """
    try:
        data = await client.search(keyword, page=page, include_stats=True)
    except KeepaError as e:
        return {"error": str(e)}

    products = data.get("products") or []
    if products:
        return {
            "keyword": keyword,
            "page": page,
            "total_results": data.get("totalResults"),
            "results": [format_search_item(p) for p in products],
            "tokens_left": data.get("tokensLeft"),
        }
    return {
        "keyword": keyword,
        "page": page,
        "total_results": data.get("totalResults"),
        "asin_list": data.get("asinList") or [],
        "tokens_left": data.get("tokensLeft"),
    }


_SORT_MAP: dict[str, list] = {
    "sales_rank_asc": ["current_SALES", "asc"],
    "sales_rank_desc": ["current_SALES", "desc"],
    "monthly_sold_desc": ["monthlySold", "desc"],
    "buy_box_price_asc": ["current_BUY_BOX_SHIPPING", "asc"],
    "buy_box_price_desc": ["current_BUY_BOX_SHIPPING", "desc"],
    "review_count_desc": ["reviewCount", "desc"],
}


@mcp.tool
async def find_products(
    min_buy_box_price: float | None = None,
    max_buy_box_price: float | None = None,
    min_sales_rank: int | None = None,
    max_sales_rank: int | None = None,
    min_monthly_sold: int | None = None,
    max_monthly_sold: int | None = None,
    category_id: int | None = None,
    brand: str | None = None,
    title_regex: str | None = None,
    min_review_count: int | None = None,
    sort_by: str = "sales_rank_asc",
    page: int = 0,
    per_page: int = 50,
) -> dict:
    """Run Keepa Product Finder against Amazon US. Returns matching ASINs.

    Filters are AND-combined. Prices in USD; sales rank is Amazon's value
    (lower = better). Use this for product-research workflows where you don't
    yet have a specific ASIN.

    Args:
        min_buy_box_price / max_buy_box_price: USD bounds on current Buy Box price.
        min_sales_rank / max_sales_rank: Bounds on current sales rank.
        min_monthly_sold / max_monthly_sold: Bounds on Keepa monthly-sold estimate.
        category_id: Numeric Amazon category (browse-node) ID.
        brand: Exact brand name (case-insensitive).
        title_regex: Regex matched against the product title.
        min_review_count: Minimum total review count.
        sort_by: One of sales_rank_asc, sales_rank_desc, monthly_sold_desc,
                 buy_box_price_asc, buy_box_price_desc, review_count_desc.
        page: Zero-indexed result page.
        per_page: Results per page (1-10000, default 50).
    """
    selection: dict[str, Any] = {
        "page": page,
        "perPage": max(1, min(per_page, 10000)),
        "sort": [_SORT_MAP.get(sort_by, _SORT_MAP["sales_rank_asc"])],
    }
    if min_buy_box_price is not None:
        selection["current_BUY_BOX_SHIPPING_gte"] = int(round(min_buy_box_price * 100))
    if max_buy_box_price is not None:
        selection["current_BUY_BOX_SHIPPING_lte"] = int(round(max_buy_box_price * 100))
    if min_sales_rank is not None:
        selection["current_SALES_gte"] = min_sales_rank
    if max_sales_rank is not None:
        selection["current_SALES_lte"] = max_sales_rank
    if min_monthly_sold is not None:
        selection["monthlySold_gte"] = min_monthly_sold
    if max_monthly_sold is not None:
        selection["monthlySold_lte"] = max_monthly_sold
    if category_id is not None:
        selection["categories_include"] = [category_id]
    if brand is not None:
        selection["brand"] = brand
    if title_regex is not None:
        selection["title"] = title_regex
    if min_review_count is not None:
        selection["reviewCount_gte"] = min_review_count

    try:
        data = await client.query(selection)
    except KeepaError as e:
        return {"error": str(e)}
    return {
        "asin_list": data.get("asinList") or [],
        "total_results": data.get("totalResults"),
        "tokens_left": data.get("tokensLeft"),
        "applied_filters": selection,
    }


@mcp.tool
async def get_price_graph(
    asin: str,
    range_days: int = 90,
    width: int = 500,
    height: int = 200,
    show_amazon: bool = True,
    show_new: bool = True,
    show_used: bool = True,
    show_buy_box: bool = True,
    show_sales_rank: bool = True,
) -> dict:
    """Build a Keepa-hosted price-history chart image URL for an ASIN (US).

    The PNG is rendered server-side by Keepa; the URL can be embedded directly.
    No API tokens are consumed for graph URLs.

    Args:
        asin: 10-character ASIN.
        range_days: 1-365, or -1 for all-time.
        width: 1-1500 pixels.
        height: 1-700 pixels.
        show_*: toggle individual price-line layers on the chart.
    """
    params = {
        "asin": asin,
        "domain": "com",
        "range": range_days,
        "width": max(1, min(width, 1500)),
        "height": max(1, min(height, 700)),
        "amazon": int(show_amazon),
        "new": int(show_new),
        "used": int(show_used),
        "bb": int(show_buy_box),
        "salesrank": int(show_sales_rank),
    }
    qs = urllib.parse.urlencode(params)
    return {"asin": asin, "graph_url": f"https://graph.keepa.com/pricehistory.png?{qs}"}


@mcp.tool
async def get_token_status() -> dict:
    """Return remaining Keepa API tokens, refill rate, and consumption stats
    for the configured KEEPA_API key. Use this to budget heavy queries.
    """
    try:
        data = await client.token()
    except KeepaError as e:
        return {"error": str(e)}
    return {
        "tokens_left": data.get("tokensLeft"),
        "refill_in_ms": data.get("refillIn"),
        "refill_rate_per_minute": data.get("refillRate"),
        "tokens_consumed_recently": data.get("tokensConsumed"),
    }


# --- ASGI app: /mcp/ for MCP traffic, /health for Render health checks ---

mcp_app = mcp.http_app(path="/mcp")


async def health(_request):
    return PlainTextResponse("ok")


@asynccontextmanager
async def lifespan(app):
    async with mcp_app.lifespan(app):
        try:
            yield
        finally:
            await client.aclose()


cors = Middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id", "Mcp-Session-Id"],
    max_age=86400,
)

app = Starlette(
    routes=[Route("/health", health), Mount("/", app=mcp_app)],
    middleware=[cors],
    lifespan=lifespan,
)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

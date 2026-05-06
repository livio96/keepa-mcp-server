"""Shape Keepa raw responses into compact, LLM-friendly summaries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Keepa Price Type index → friendly name (subset; covers everything we surface).
PRICE_TYPES: dict[int, str] = {
    0: "amazon",
    1: "new",
    2: "used",
    3: "sales_rank",
    5: "collectible",
    6: "refurbished",
    9: "warehouse",
    10: "new_fba",
    11: "count_new",
    12: "count_used",
    13: "count_refurbished",
    14: "count_collectible",
    16: "rating",
    17: "review_count",
    18: "buy_box",
    19: "used_like_new",
    20: "used_very_good",
    21: "used_good",
    22: "used_acceptable",
    23: "refurbished_amazon",
    25: "buy_box_used",
    28: "new_fbm",
    29: "new_fba_alt",
}

# Price-type indices we expose in the per-product summary, in display order.
SUMMARY_PRICE_INDICES: list[int] = [18, 0, 1, 2, 10]  # buy_box, amazon, new, used, new_fba


def keepa_minutes_to_iso(km: int | None) -> str | None:
    """Keepa time = unix-minutes minus 21564000. Convert to ISO8601 UTC."""
    if km is None or km < 0:
        return None
    unix_seconds = (km + 21564000) * 60
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()


def cents_to_usd(c: int | None) -> float | None:
    if c is None or c < 0:
        return None
    return round(c / 100, 2)


def _value_at(arr: list | None, idx: int, *, is_price: bool = True):
    """Pick stats[idx] from a 1D price-type-indexed array (current/avg/avg30/...)."""
    if not arr or idx >= len(arr):
        return None
    v = arr[idx]
    if v is None or v < 0:
        return None
    return cents_to_usd(v) if is_price else v


def _oos_pct_at(arr: list | None, idx: int) -> int | None:
    """Out-of-stock percentage for a price type. -1 means no data."""
    if not arr or idx >= len(arr):
        return None
    v = arr[idx]
    if v is None or v < 0:
        return None
    return int(v)


def _extreme(arr: list | None, idx: int, *, is_price: bool = True) -> dict | None:
    """Pick stats[idx] from min/max/minInInterval/maxInInterval (each is [time, value] or null)."""
    if not arr or idx >= len(arr):
        return None
    entry = arr[idx]
    if not entry or len(entry) < 2:
        return None
    when, val = entry[0], entry[1]
    if val is None or val < 0:
        return None
    return {
        "value": cents_to_usd(val) if is_price else val,
        "at": keepa_minutes_to_iso(when),
    }


def _category_path(p: dict) -> list[str] | None:
    tree = p.get("categoryTree") or []
    names = [c.get("name") for c in tree if isinstance(c, dict) and c.get("name")]
    return names or None


def _first_image(p: dict) -> str | None:
    csv = p.get("imagesCSV") or ""
    first = csv.split(",")[0].strip() if csv else ""
    return f"https://images-na.ssl-images-amazon.com/images/I/{first}" if first else None


def format_product(p: dict) -> dict:
    """Compact summary of a Keepa product object (single product)."""
    stats = p.get("stats") or {}

    prices: dict[str, Any] = {}
    for idx in SUMMARY_PRICE_INDICES:
        name = PRICE_TYPES.get(idx, f"type_{idx}")
        prices[name] = {
            "current": _value_at(stats.get("current"), idx),
            "avg_30": _value_at(stats.get("avg30"), idx),
            "avg_90": _value_at(stats.get("avg90"), idx),
            "avg_180": _value_at(stats.get("avg180"), idx),
            "avg_365": _value_at(stats.get("avg365"), idx),
            "all_time_min": _extreme(stats.get("min"), idx),
            "all_time_max": _extreme(stats.get("max"), idx),
            "interval_min": _extreme(stats.get("minInInterval"), idx),
            "interval_max": _extreme(stats.get("maxInInterval"), idx),
            "oos_30d_pct": _oos_pct_at(stats.get("outOfStockPercentage30"), idx),
            "oos_90d_pct": _oos_pct_at(stats.get("outOfStockPercentage90"), idx),
            "oos_365d_pct": _oos_pct_at(stats.get("outOfStockPercentage365"), idx),
        }

    sales_rank = {
        "current": _value_at(stats.get("current"), 3, is_price=False),
        "avg_30": _value_at(stats.get("avg30"), 3, is_price=False),
        "avg_90": _value_at(stats.get("avg90"), 3, is_price=False),
        "avg_180": _value_at(stats.get("avg180"), 3, is_price=False),
        "avg_365": _value_at(stats.get("avg365"), 3, is_price=False),
        "all_time_min": _extreme(stats.get("min"), 3, is_price=False),
        "all_time_max": _extreme(stats.get("max"), 3, is_price=False),
        "interval_min": _extreme(stats.get("minInInterval"), 3, is_price=False),
        "interval_max": _extreme(stats.get("maxInInterval"), 3, is_price=False),
    }

    buy_box = {
        "is_amazon": stats.get("buyBoxIsAmazon"),
        "current_seller_id": stats.get("buyBoxSellerId"),
        "is_fba": stats.get("buyBoxIsFBA"),
        "is_unqualified": stats.get("buyBoxIsUnqualified"),
        "stats_per_seller": stats.get("buyBoxStats"),
    }

    offer_counts = {
        "new": _value_at(stats.get("current"), 11, is_price=False),
        "used": _value_at(stats.get("current"), 12, is_price=False),
        "refurbished": _value_at(stats.get("current"), 13, is_price=False),
        "collectible": _value_at(stats.get("current"), 14, is_price=False),
    }

    rating_raw = _value_at(stats.get("current"), 16, is_price=False)
    rating = round(rating_raw / 10, 1) if rating_raw is not None else None

    monthly_sold = p.get("monthlySold")
    if monthly_sold is None or monthly_sold < 0:
        monthly_sold = None

    return {
        "asin": p.get("asin"),
        "title": p.get("title"),
        "brand": p.get("brand"),
        "manufacturer": p.get("manufacturer"),
        "model": p.get("model"),
        "part_number": p.get("partNumber"),
        "category_path": _category_path(p),
        "root_category_id": p.get("rootCategory"),
        "upc_codes": p.get("upcList") or [],
        "ean_codes": p.get("eanList") or [],
        "image_url": _first_image(p),
        "monthly_sold": monthly_sold,
        "rating": rating,
        "review_count": _value_at(stats.get("current"), 17, is_price=False),
        "prices": prices,
        "sales_rank": sales_rank,
        "buy_box": buy_box,
        "offer_counts": offer_counts,
    }


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v}%" if isinstance(v, (int, float)) else "—"


def _fmt_int(v) -> str:
    return f"{v:,}" if isinstance(v, (int, float)) else "—"


def _fmt_extreme(e: dict | None, *, money: bool = True) -> str:
    if not e:
        return "—"
    val = e.get("value")
    at = (e.get("at") or "")[:10]
    if val is None:
        return "—"
    formatted = _fmt_money(val) if money else _fmt_int(val)
    return f"{formatted} ({at})" if at else formatted


_PRICE_DISPLAY_ORDER = [
    ("buy_box", "Buy Box"),
    ("amazon", "Amazon"),
    ("new", "New"),
    ("used", "Used"),
    ("new_fba", "New FBA"),
]


def build_summary_table(s: dict) -> str:
    """Keepa-style markdown table summary for a product."""
    title = s.get("title") or s.get("asin") or "(unknown)"
    sr = s.get("sales_rank") or {}
    bb = s.get("buy_box") or {}
    oc = s.get("offer_counts") or {}

    lines: list[str] = [
        f"## {title}",
        "",
        f"**ASIN:** `{s.get('asin')}`  ·  **Brand:** {s.get('brand') or '—'}  ·  "
        f"**Monthly Sold:** {_fmt_int(s.get('monthly_sold'))}",
        f"**Rating:** {s.get('rating') if s.get('rating') is not None else '—'}  ·  "
        f"**Reviews:** {_fmt_int(s.get('review_count'))}  ·  "
        f"**Sales Rank (current):** {_fmt_int(sr.get('current'))}",
        "",
        "### Price History",
        "",
        "| Metric | " + " | ".join(label for _, label in _PRICE_DISPLAY_ORDER) + " |",
        "|---" * (len(_PRICE_DISPLAY_ORDER) + 1) + "|",
    ]

    rows: list[tuple[str, callable]] = [
        ("Current",            lambda p: _fmt_money(p.get("current"))),
        ("90-day avg",         lambda p: _fmt_money(p.get("avg_90"))),
        ("180-day avg",        lambda p: _fmt_money(p.get("avg_180"))),
        ("365-day avg",        lambda p: _fmt_money(p.get("avg_365"))),
        ("Lowest (all-time)",  lambda p: _fmt_extreme(p.get("all_time_min"))),
        ("Lowest (365 days)",  lambda p: _fmt_extreme(p.get("interval_min"))),
        ("Highest (all-time)", lambda p: _fmt_extreme(p.get("all_time_max"))),
        ("Highest (365 days)", lambda p: _fmt_extreme(p.get("interval_max"))),
        ("OOS % (30d)",        lambda p: _fmt_pct(p.get("oos_30d_pct"))),
        ("OOS % (90d)",        lambda p: _fmt_pct(p.get("oos_90d_pct"))),
        ("OOS % (365d)",       lambda p: _fmt_pct(p.get("oos_365d_pct"))),
    ]

    for label, fn in rows:
        cells = " | ".join(fn(s["prices"].get(k, {})) for k, _ in _PRICE_DISPLAY_ORDER)
        lines.append(f"| **{label}** | {cells} |")

    lines += [
        "",
        "### Sales Rank",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Current | {_fmt_int(sr.get('current'))} |",
        f"| 30-day avg | {_fmt_int(sr.get('avg_30'))} |",
        f"| 90-day avg | {_fmt_int(sr.get('avg_90'))} |",
        f"| 180-day avg | {_fmt_int(sr.get('avg_180'))} |",
        f"| 365-day avg | {_fmt_int(sr.get('avg_365'))} |",
        f"| Lowest (all-time) | {_fmt_extreme(sr.get('all_time_min'), money=False)} |",
        f"| Lowest (365 days) | {_fmt_extreme(sr.get('interval_min'), money=False)} |",
        f"| Highest (all-time) | {_fmt_extreme(sr.get('all_time_max'), money=False)} |",
        f"| Highest (365 days) | {_fmt_extreme(sr.get('interval_max'), money=False)} |",
        "",
        "### Buy Box & Offers",
        "",
        f"- **Current Buy Box seller:** {bb.get('current_seller_id') or '—'}  ·  "
        f"**Is Amazon:** {bb.get('is_amazon')}  ·  **Is FBA:** {bb.get('is_fba')}",
        f"- **Offer counts** — New: {_fmt_int(oc.get('new'))}  ·  "
        f"Used: {_fmt_int(oc.get('used'))}  ·  "
        f"Refurbished: {_fmt_int(oc.get('refurbished'))}  ·  "
        f"Collectible: {_fmt_int(oc.get('collectible'))}",
    ]
    return "\n".join(lines)


def format_search_item(it: dict) -> dict:
    stats = (it or {}).get("stats") or {}
    monthly_sold = it.get("monthlySold")
    if monthly_sold is None or monthly_sold < 0:
        monthly_sold = None
    return {
        "asin": it.get("asin"),
        "title": it.get("title"),
        "brand": it.get("brand"),
        "current_buy_box": _value_at(stats.get("current"), 18),
        "current_new": _value_at(stats.get("current"), 1),
        "sales_rank": _value_at(stats.get("current"), 3, is_price=False),
        "monthly_sold": monthly_sold,
    }

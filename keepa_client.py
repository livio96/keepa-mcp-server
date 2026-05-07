"""Async client for the Keepa REST API (US marketplace, domain=1)."""
from __future__ import annotations

import json as _json
from typing import Any

import httpx

KEEPA_BASE = "https://api.keepa.com"
DOMAIN_US = 1


class KeepaError(Exception):
    """Raised for any non-2xx response or transport failure from the Keepa API."""


class KeepaClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout, base_url=KEEPA_BASE)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        params = {"key": self.api_key, **params}
        try:
            r = await self._client.get(path, params=params)
        except httpx.RequestError as e:
            raise KeepaError(f"Keepa API unreachable: {e}") from e

        if r.status_code == 403:
            raise KeepaError("Invalid Keepa API key (403)")
        if r.status_code == 429:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise KeepaError(
                f"Keepa rate limit / out of tokens. refillIn={body.get('refillIn')}ms"
            )
        if r.status_code >= 400:
            raise KeepaError(f"Keepa API error {r.status_code}: {r.text[:300]}")

        try:
            return r.json()
        except ValueError as e:
            raise KeepaError(f"Keepa returned non-JSON response: {e}") from e

    async def product(
        self,
        *,
        asin: str | None = None,
        code: str | None = None,
        stats_days: int = 90,
        offers: int = 20,
        history: bool = True,
    ) -> dict:
        if not asin and not code:
            raise ValueError("product() requires asin or code")
        params: dict[str, Any] = {
            "domain": DOMAIN_US,
            "stats": stats_days,
            "buybox": 1,
            "offers": offers,
            "rating": 1,
            "history": 1 if history else 0,
        }
        if asin:
            params["asin"] = asin
        else:
            params["code"] = code
        return await self._get("/product", params)

    async def search(
        self, term: str, page: int = 0, *, include_stats: bool = True
    ) -> dict:
        params: dict[str, Any] = {
            "domain": DOMAIN_US,
            "type": "product",
            "term": term,
            "page": page,
        }
        if include_stats:
            params["stats"] = 90
        return await self._get("/search", params)

    async def query(self, selection: dict) -> dict:
        return await self._get(
            "/query",
            {"domain": DOMAIN_US, "selection": _json.dumps(selection)},
        )

    async def token(self) -> dict:
        return await self._get("/token", {})

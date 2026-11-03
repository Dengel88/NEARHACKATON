"""Live smoke check against the real 1Click API. Moves no funds.

    python scripts/check_live.py              # tokens + dry quotes for a $1 invoice
    python scripts/check_live.py --origin-chain sol --origin-symbol SOL

1. GET /v0/tokens: confirms USDC_ASSET_ID really is USDC on NEAR and that every
   `payments.offered` entry in config.yaml exists.
2. POST /v0/quote with dry=true, EXACT_OUTPUT $1 USDC to MERCHANT_NEAR_ACCOUNT:
   prints how much of the origin token the customer would pay. Dry quotes never
   return a deposit address, so nothing can be sent.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings  # noqa: E402
from app.domain import build_quote_request  # noqa: E402
from app.oneclick import LiveOneClick, OneClickError  # noqa: E402

# Refund address per origin chain for dry quotes only (any syntactically valid one).
REFUND = {
    "near": "wanderlust.near",
    "eth": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "base": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "arb": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "sol": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "tron": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
    "btc": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usd", default="1.00")
    ap.add_argument("--origin-chain")
    ap.add_argument("--origin-symbol")
    a = ap.parse_args()

    s = load_settings()
    oc = LiveOneClick(s.oneclick_base_url, s.oneclick_api_key)
    ok = True

    tokens = await oc.tokens()
    print(f"/v0/tokens: {len(tokens)} tokens")
    by_key = {(t.get("blockchain"), t.get("symbol")): t for t in tokens}
    usdc_near = [t for t in tokens if t.get("blockchain") == "near" and t.get("symbol") == "USDC"]
    match = any(t.get("assetId") == s.usdc_asset_id for t in usdc_near)
    print(f"USDC on NEAR: {[t.get('assetId') for t in usdc_near]}")
    print(f"USDC_ASSET_ID {s.usdc_asset_id}: {'OK' if match else 'MISMATCH'}")
    ok &= match

    offered = s.payments.get("offered", [])
    missing = [o for o in offered if (o["blockchain"], o["symbol"]) not in by_key]
    for o in offered:
        t = by_key.get((o["blockchain"], o["symbol"]))
        print(f"  offered {o['blockchain']:>5}/{o['symbol']:<6} {'-> ' + t['assetId'] if t else 'NOT FOUND'}")
    ok &= not missing

    units = int((float(a.usd) * 10 ** int(s.payments.get("settlement_decimals", 6))) + 0.5)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    targets = [(a.origin_chain, a.origin_symbol)] if a.origin_chain else [(o["blockchain"], o["symbol"]) for o in offered]
    print(f"\nDry quotes: EXACT_OUTPUT {a.usd} USDC -> {s.merchant_near_account}")
    for chain, sym in targets:
        t = by_key.get((chain, sym))
        if not t:
            continue
        req = build_quote_request(s, origin_asset=t["assetId"], refund_to=REFUND.get(chain, "wanderlust.near"),
                                  amount_units=units, deadline_iso=deadline, dry=True)
        try:
            q = (await oc.quote(req)).get("quote", {})
            print(f"  {chain:>5}/{sym:<6} pay {q.get('amountInFormatted')} (~${q.get('amountInUsd')}), "
                  f"merchant gets {q.get('amountOutFormatted')} USDC")
        except OneClickError as exc:
            limit = "minimum" in str(exc) or "too low" in str(exc)
            ok &= limit  # a route minimum above the invoice is a business limit, not a bug
            print(f"  {chain:>5}/{sym:<6} {'MIN LIMIT' if limit else 'ERROR'} {exc}")
    print("\nRESULT:", "OK" if ok else "CHECK ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

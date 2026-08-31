"""Explicit, manually-triggered integration check against the REAL
Razorpay Test Mode API. Never runs as part of the normal test suite or CI
— it is not named `test_*.py` so pytest's default collection skips it, and
`.github/workflows/ci.yml` never invokes it. CI always uses
FakePaymentProvider; this script is the only thing in the repo that talks
to a real (Test Mode) Razorpay account.

Usage (from backend/, with RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET set):
    python -m tests.integration.razorpay_live_check

What it does, in order, reporting exactly what happened at each step:
  1. Verifies credentials are configured and RAZORPAY_MODE == "test".
  2. Creates exactly ONE small Payment Link (respects Razorpay's
     documented Test Mode limit of 30 links per business — this script
     never creates more than one per invocation).
  3. Fetches it back by ID to confirm the create+fetch round trip works.
  4. Fetches it again by reference_id, to confirm the reconciliation path
     execution_service depends on after an ambiguous result also works
     against the real API.
  5. Prints the short_url. It does NOT attempt to pay it — completing a
     Test Mode payment requires a human using Razorpay's documented test
     card/UPI details in a browser; that's a manual follow-up, not
     something this script fakes or simulates.

Never combine this script's output with evaluation/'s synthetic numbers —
see docs/razorpay-integration.md "Simulated vs. real evaluation."
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.domain.providers.base import CreatePaymentLinkRequest, PaymentProviderError
from app.payments.providers.razorpay import RazorpayModeError, RazorpayPaymentProvider


async def main() -> int:
    settings = get_settings()

    print(f"RAZORPAY_MODE = {settings.razorpay_mode!r}")
    print(f"RAZORPAY_BASE_URL = {settings.razorpay_base_url!r}")
    print(f"Credentials configured: {bool(settings.razorpay_key_id and settings.razorpay_key_secret)}")

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        print("\nABORTED: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set. Nothing was called.")
        return 1

    try:
        provider = RazorpayPaymentProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            base_url=settings.razorpay_base_url,
            mode=settings.razorpay_mode,
            timeout_seconds=settings.razorpay_request_timeout_seconds,
        )
    except RazorpayModeError as exc:
        print(f"\nABORTED: {exc}")
        return 1

    reference_id = f"recoverai-live-check-{int(datetime.now(UTC).timestamp())}"
    request = CreatePaymentLinkRequest(
        reference_id=reference_id,
        amount=100,  # smallest sensible Test Mode amount: ₹1.00
        currency="INR",
        description="RecoverAI Razorpay Test Mode integration check",
        expire_by=datetime.now(UTC) + timedelta(hours=1),
    )

    print(f"\nCreating one Payment Link (reference_id={reference_id!r}, amount=100 paise)...")
    try:
        link = await provider.create_payment_link(request)
    except PaymentProviderError as exc:
        print(f"\nFAILED to create Payment Link: {type(exc).__name__}: {exc}")
        return 1

    print(f"  id: {link.provider_reference}")
    print(f"  short_url: {link.short_url}")
    print(f"  status: {link.status.value}")

    print("\nFetching it back by ID...")
    fetched = await provider.fetch_payment_link(link.provider_reference)
    print(f"  status: {fetched.status.value}, amount_paid: {fetched.amount_paid}")

    print("\nFetching it back by reference_id (the reconciliation path)...")
    reconciled = await provider.find_payment_link_by_reference(reference_id)
    if reconciled is None:
        print("  FAILED: not found by reference_id — reconciliation would not have worked.")
        return 1
    matches = reconciled.provider_reference == link.provider_reference
    print(f"  found: {reconciled.provider_reference} (matches: {matches})")

    print(
        f"\nDONE. 1 Payment Link created and verified. To manually complete the payment, "
        f"open {link.short_url} and use a Razorpay Test Mode test card/UPI ID."
    )
    print("This was NOT combined with any simulated evaluation numbers — report it separately.")
    await provider.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

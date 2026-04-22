"""
One-off: fetch eBay fulfillment, payment, and return policies for EBAY_US,
print all IDs/names, then write the policy IDs back to .env.

Forces production environment regardless of .env EBAY_ENVIRONMENT setting.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from packages.core.src.config import get_settings
from packages.ebay.src.auth import EbayAuth

# ── Auth setup ────────────────────────────────────────────────────────────────

settings = get_settings()
settings.__dict__["ebay_environment"] = "production"

auth = EbayAuth()
token = auth.get_user_token()

if not token:
    print("ERROR: No production token found. Set EBAY_PROD_USER_TOKEN in .env.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

BASE = "https://api.ebay.com/sell/account/v1"
MARKETPLACE = "EBAY_US"

# ── Policy endpoint definitions ───────────────────────────────────────────────

POLICY_TYPES = [
    (
        "Fulfillment",
        f"{BASE}/fulfillment_policy?marketplace_id={MARKETPLACE}",
        "fulfillmentPolicies",
        "fulfillmentPolicyId",
        "EBAY_FULFILLMENT_POLICY_ID",
    ),
    (
        "Payment",
        f"{BASE}/payment_policy?marketplace_id={MARKETPLACE}",
        "paymentPolicies",
        "paymentPolicyId",
        "EBAY_PAYMENT_POLICY_ID",
    ),
    (
        "Return",
        f"{BASE}/return_policy?marketplace_id={MARKETPLACE}",
        "returnPolicies",
        "returnPolicyId",
        "EBAY_RETURN_POLICY_ID",
    ),
]

# ── Fetch and print ───────────────────────────────────────────────────────────

collected: dict[str, str] = {}   # env_key → first policy ID

for label, url, list_key, id_field, env_key in POLICY_TYPES:
    print(f"-- {label} Policies --")
    resp = httpx.get(url, headers=HEADERS, timeout=20)
    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text}\n")
        continue

    policies = resp.json().get(list_key, [])
    if not policies:
        print("  (none found)\n")
        continue

    print(f"Found {len(policies)} policy/policies:\n")
    for p in policies:
        pid  = p.get(id_field, "")
        name = p.get("name", "")
        print(f"  ID:   {pid}")
        print(f"  Name: {name}")
        print()
        if not collected.get(env_key):
            collected[env_key] = pid   # keep first as the active policy

# ── Write IDs back to .env ────────────────────────────────────────────────────

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

# EBAY_FULFILLMENT_POLICY_ID is already known; keep it even if the API returned
# nothing (e.g. Business Policies not enrolled)
KNOWN_OVERRIDES = {
    "EBAY_FULFILLMENT_POLICY_ID": "287672421015",
}
for key, val in KNOWN_OVERRIDES.items():
    if not collected.get(key):
        collected[key] = val

if not collected:
    print("No policy IDs to write — .env unchanged.")
    sys.exit(0)

# Read current .env, update or append each key
if ENV_PATH.exists():
    env_text = ENV_PATH.read_text(encoding="utf-8")
else:
    env_text = ""

env_lines = env_text.splitlines(keepends=True)

for env_key, policy_id in collected.items():
    if not policy_id:
        continue
    pattern = re.compile(rf"^{re.escape(env_key)}\s*=.*$", re.MULTILINE)
    replacement = f"{env_key}={policy_id}"
    if pattern.search(env_text):
        env_text = pattern.sub(replacement, env_text)
        print(f"Updated  {env_key}={policy_id}")
    else:
        # Append after the last EBAY_ line, or at end of file
        lines = env_text.splitlines(keepends=True)
        insert_at = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("EBAY_"):
                insert_at = i + 1
                break
        lines.insert(insert_at, f"{replacement}\n")
        env_text = "".join(lines)
        print(f"Added    {env_key}={policy_id}")

ENV_PATH.write_text(env_text, encoding="utf-8")
print(f"\n.env updated at {ENV_PATH}")

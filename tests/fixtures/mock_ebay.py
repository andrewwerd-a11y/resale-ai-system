"""Mock eBay API response payloads for testing."""

INVENTORY_ITEM_SUCCESS: dict = {"status": 204}

OFFER_CREATE_SUCCESS: dict = {"offerId": "test-offer-123"}

PUBLISH_SUCCESS: dict = {"listingId": "test-listing-456"}

ORDER_RESPONSE: dict = {
    "orders": [
        {
            "orderId": "order-001",
            "lineItems": [
                {
                    "sku": "CL-000001",
                    "title": "Nike Jacket Blue L",
                    "quantity": 1,
                    "lineItemCost": {"value": "24.00", "currency": "USD"},
                }
            ],
            "pricingSummary": {
                "total": {"value": "24.00", "currency": "USD"},
            },
            "creationDate": "2026-03-15T12:00:00.000Z",
            "orderFulfillmentStatus": "NOT_STARTED",
            "orderPaymentStatus": "PAID",
        }
    ],
    "total": 1,
}

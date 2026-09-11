"""Explicit actor-visible reason allowlist; no automatic extension diagnostics."""

# Only documented actor-facing M2/M5/M6 codes may cross the Game boundary. Extension
# diagnostics default to a generic status until an actor-facing contract exists.
VISIBLE_DOMAIN_REASONS = frozenset(
    {
        "INVALID_PAYLOAD",
        "INVALID_DURATION",
        "UNKNOWN_ACTOR",
        "INVALID_ENTITY",
        "MISSING_POSITION",
        "INVALID_POSITION",
        "UNKNOWN_ROUTE",
        "INVALID_ROUTE",
        "UNKNOWN_LOCATION",
        "INVALID_LOCATION",
        "WRONG_ORIGIN",
        "ROUTE_IMPASSABLE",
        "INVALID_TRAVERSAL_COST",
        "INVALID_PASSABILITY",
        "ROUTE_CHANGED",
        "SELF_TARGET",
        "UNKNOWN_TARGET",
        "INVALID_TARGET",
        "TARGET_MISSING_POSITION",
        "TARGET_INVALID_POSITION",
        "OUT_OF_RANGE",
        "INVALID_QUANTITY",
        "UNKNOWN_ITEM",
        "UNKNOWN_INVENTORY_OWNER",
        "INVALID_INVENTORY_STATE",
        "INSUFFICIENT_QUANTITY",
        "MISSING_SURVIVAL_STATE",
        "INVALID_SURVIVAL_STATE",
        "ITEM_NOT_CONSUMABLE",
        "INVALID_WALLET",
        "MISSING_WALLET",
        "UNKNOWN_OFFER",
        "INVALID_OFFER",
        "OFFER_UNAVAILABLE",
        "SELF_PURCHASE",
        "INSUFFICIENT_FUNDS",
        "INSUFFICIENT_STOCK",
        "OFFER_CHANGED",
        "UNKNOWN_SELLER",
        "INVALID_SELLER",
        "SELLER_MISSING_POSITION",
        "SELLER_INVALID_POSITION",
    }
)

# BUY's shared resource validators cannot attribute these diagnostics to the
# buyer versus seller. Hide private seller table existence/shape uniformly.
PRIVATE_PURCHASE_REASONS = frozenset(
    {
        "INVALID_WALLET",
        "MISSING_WALLET",
        "INVALID_INVENTORY_STATE",
        "UNKNOWN_INVENTORY_OWNER",
        "UNKNOWN_ITEM",
    }
)

AUTHORITY_REASONS = frozenset(
    {
        "WRONG_RUN",
        "WRONG_ACTOR",
        "UNKNOWN_OBSERVATION",
        "UNAUTHORIZED_OBSERVATION",
        "INVALID_SUBMISSION_TIME",
        "INVALID_CLAIM_REFERENCE",
        "INVALID_REPLY_REFERENCE",
    }
)
AVAILABILITY_REASONS = frozenset({"UNKNOWN_ACTION", "SOCIAL_UNAVAILABLE"})
IDEMPOTENCY_REASONS = frozenset({"REQUEST_ID_CONFLICT"})
FALLBACK_REASONS = frozenset({"ACTION_REJECTED", "ACTION_FAILED"})
GAME_ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "GAME_UNAVAILABLE",
        "OBSERVATION_UNAVAILABLE",
        "KNOWLEDGE_UNAVAILABLE",
        "ENGINE_ERROR",
    }
)
TURN_FAILURE_CODES = frozenset(
    {
        "OBSERVATION_UNAVAILABLE",
        "CONTROLLER_ERROR",
        "INVALID_CONTROLLER_OUTPUT",
        "UNKNOWN_ACTION",
        "INVALID_PAYLOAD",
        "INVALID_DURATION",
        "INVALID_QUANTITY",
        "SUBMISSION_ERROR",
    }
)

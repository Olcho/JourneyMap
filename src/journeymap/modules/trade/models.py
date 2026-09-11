"""Trade-owned wallet/offer schema and detached currency transfer."""

from collections.abc import Iterable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.handlers import ActionValidationError


@dataclass(frozen=True, slots=True)
class Wallet:
    actor_id: str
    balance: int

    def __post_init__(self) -> None:
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ValueError("invalid wallet owner")
        if type(self.balance) is not int or self.balance < 0:
            raise ValueError("balance must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class Offer:
    offer_id: str
    seller_id: str
    item_id: str
    unit_price: int
    active: bool = True

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.offer_id, self.seller_id, self.item_id)
        ):
            raise ValueError("invalid offer identity")
        if type(self.unit_price) is not int or self.unit_price < 0:
            raise ValueError("unit_price must be a non-negative integer")
        if type(self.active) is not bool:
            raise ValueError("active must be boolean")

    def to_json(self) -> JsonObject:
        return {
            "offer_id": self.offer_id,
            "seller_id": self.seller_id,
            "item_id": self.item_id,
            "unit_price": self.unit_price,
            "active": self.active,
        }


def trade_state(wallets: Iterable[Wallet], offers: Iterable[Offer]) -> JsonObject:
    balances: JsonObject = {}
    listings: JsonObject = {}
    for wallet in wallets:
        if wallet.actor_id in balances:
            raise ValueError("duplicate wallet")
        balances[wallet.actor_id] = wallet.balance
    for offer in offers:
        if offer.offer_id in listings:
            raise ValueError("duplicate offer")
        if offer.seller_id not in balances:
            raise ValueError("offer seller requires a wallet")
        listings[offer.offer_id] = offer.to_json()
    return {"wallets": balances, "offers": listings}


def wallet_balance(state: JsonObject, actor: str) -> int:
    trade = state.get("trade")
    wallets = trade.get("wallets") if isinstance(trade, dict) else None
    if not isinstance(wallets, dict):
        raise ActionValidationError("INVALID_WALLET")
    if actor not in wallets:
        raise ActionValidationError("MISSING_WALLET")
    balance = wallets[actor]
    if not isinstance(balance, int) or isinstance(balance, bool) or balance < 0:
        raise ActionValidationError("INVALID_WALLET")
    return balance


def get_offer(state: JsonObject, offer_id: str) -> Offer:
    trade = state.get("trade")
    offers = trade.get("offers") if isinstance(trade, dict) else None
    if not isinstance(offers, dict):
        raise ActionValidationError("INVALID_OFFER")
    if offer_id not in offers:
        raise ActionValidationError("UNKNOWN_OFFER")
    row = offers[offer_id]
    if not isinstance(row, dict) or row.get("offer_id") != offer_id:
        raise ActionValidationError("INVALID_OFFER")
    seller, item = row.get("seller_id"), row.get("item_id")
    price, active = row.get("unit_price"), row.get("active")
    if (
        not isinstance(seller, str)
        or not isinstance(item, str)
        or not isinstance(price, int)
        or not isinstance(active, bool)
    ):
        raise ActionValidationError("INVALID_OFFER")
    try:
        return Offer(offer_id, seller, item, price, active)
    except ValueError as error:
        raise ActionValidationError("INVALID_OFFER") from error


def payment_candidate(state: JsonObject, buyer: str, seller: str, total: int) -> JsonObject:
    if type(total) is not int or total < 0:
        raise ActionValidationError("INVALID_PRICE")
    if buyer == seller:
        raise ActionValidationError("SELF_PURCHASE")
    balance = wallet_balance(state, buyer)
    if balance < total:
        raise ActionValidationError("INSUFFICIENT_FUNDS")
    trade = state["trade"]
    assert isinstance(trade, dict)
    candidate = clone_json_object(trade)
    wallets = candidate["wallets"]
    assert isinstance(wallets, dict)
    wallets[buyer] = balance - total
    # Even a defect in the second side cannot mutate the input or kernel.
    wallets[seller] = wallet_balance(state, seller) + total
    return candidate

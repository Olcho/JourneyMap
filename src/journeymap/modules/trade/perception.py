"""Public local offers never imply access to seller wallets or stock tables."""

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.entities import get_entity
from journeymap.modules.inventory.models import item_definition
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.trade.models import get_offer, wallet_balance


def perceive_trade(state: JsonObject, actor_id: str) -> JsonObject:
    balance = wallet_balance(state, actor_id)
    location = perceive_position(state, actor_id).get("location_id")
    trade = state.get("trade")
    offers = trade.get("offers") if isinstance(trade, dict) else None
    if not isinstance(offers, dict):
        raise ValueError("invalid offers")
    visible: list[JsonValue] = []
    if location is not None:
        for offer_id, row in sorted(offers.items()):
            if not isinstance(row, dict) or row.get("active") is not True:
                continue
            seller = row.get("seller_id")
            if not isinstance(seller, str) or seller == actor_id:
                continue
            if get_entity(state, seller) is None:
                continue
            if perceive_position(state, seller).get("location_id") != location:
                continue
            offer = get_offer(state, offer_id)
            item_definition(state, offer.item_id)
            visible.append(offer.to_json())
    return {"wallet": balance, "offers": visible}

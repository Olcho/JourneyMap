"""Timed local purchase with an immutable start quote and current resource checks."""

from journeymap.core.actions import validate_actor
from journeymap.core.canonical import JsonObject
from journeymap.core.entities import get_entity
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ActionRequest,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.modules.inventory.models import positive_quantity
from journeymap.modules.inventory.transitions import transfer_candidate
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.trade.models import Offer, get_offer, payment_candidate


def validate_buy_payload(payload: JsonObject) -> tuple[str, int]:
    if set(payload) != {"offer_id", "quantity"}:
        raise ActionValidationError("INVALID_PAYLOAD")
    offer_id = payload["offer_id"]
    if not isinstance(offer_id, str) or not offer_id:
        raise ActionValidationError("INVALID_PAYLOAD")
    return offer_id, positive_quantity(payload["quantity"])


def _purchase(request: ActionRequest) -> tuple[str, int]:
    return validate_buy_payload(request.payload)


def _local_offer(request: ActionRequest, context: ValidationContext) -> Offer:
    offer_id, _ = _purchase(request)
    validate_actor(request, context)
    offer = get_offer(context.state, offer_id)
    if not offer.active:
        raise ActionValidationError("OFFER_UNAVAILABLE")
    if request.actor_id == offer.seller_id:
        raise ActionValidationError("SELF_PURCHASE")
    try:
        seller = get_entity(context.state, offer.seller_id)
    except ValueError as error:
        raise ActionValidationError("INVALID_SELLER") from error
    if seller is None:
        raise ActionValidationError("UNKNOWN_SELLER")
    locations = []
    for actor, prefix in ((request.actor_id, ""), (offer.seller_id, "SELLER_")):
        try:
            position = perceive_position(context.state, actor)
        except ValueError as error:
            raise ActionValidationError(f"{prefix}INVALID_POSITION") from error
        if not position:
            raise ActionValidationError(f"{prefix}MISSING_POSITION")
        locations.append(position["location_id"])
    if locations[0] != locations[1]:
        raise ActionValidationError("OUT_OF_RANGE")
    return offer


def _inventory(request: ActionRequest, context: ValidationContext, offer: Offer) -> JsonObject:
    # The inventory module owns all quantity shape and conservation rules.
    _, quantity = _purchase(request)
    try:
        return transfer_candidate(
            context.state, offer.seller_id, request.actor_id, offer.item_id, quantity
        )
    except ActionValidationError as error:
        if error.reason_code == "INSUFFICIENT_QUANTITY":
            raise ActionValidationError("INSUFFICIENT_STOCK") from error
        raise


class BuyHandler:
    handler_id = "trade.buy.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        offer = _local_offer(request, context)
        _, quantity = _purchase(request)
        _inventory(request, context, offer)
        payment_candidate(
            context.state, request.actor_id, offer.seller_id, offer.unit_price * quantity
        )

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        offer_id, _ = _purchase(request)
        return ActionTiming(1, get_offer(context.state, offer_id).to_json())

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        offer_id, _ = _purchase(request)
        offer = get_offer(context.state, offer_id)
        if not offer.active:
            raise ActionValidationError("OFFER_UNAVAILABLE")
        if offer.to_json() != timing.data:
            raise ActionValidationError("OFFER_CHANGED")
        self.validate(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        validation = ValidationContext(context.run_id, context.simulation_time, context.state)
        offer = _local_offer(request, validation)
        _, quantity = _purchase(request)
        inventory = _inventory(request, validation, offer)
        total = offer.unit_price * quantity
        trade = payment_candidate(context.state, request.actor_id, offer.seller_id, total)
        return TransitionPlan(
            set_values={"inventory": inventory, "trade": trade},
            events=(
                EventDraft(
                    "ItemPurchased",
                    1,
                    {
                        "buyer_id": request.actor_id,
                        "seller_id": offer.seller_id,
                        "offer_id": offer.offer_id,
                        "item_id": offer.item_id,
                        "quantity": quantity,
                        "unit_price": offer.unit_price,
                        "total_price": total,
                    },
                ),
            ),
        )

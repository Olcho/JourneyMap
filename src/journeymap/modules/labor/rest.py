"""Life opt-in spatial rest constraint around the unchanged survival handler."""

from journeymap.core.handlers import ActionRequest, ActionValidationError, ValidationContext
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.survival.handlers import RestHandler


class HomeRestHandler(RestHandler):
    handler_id = "labor.home-rest.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        super().validate(request, context)
        survival = context.state.get("survival")
        homes = survival.get("rest_locations") if isinstance(survival, dict) else None
        home = homes.get(request.actor_id) if isinstance(homes, dict) else None
        if (
            not isinstance(home, str)
            or perceive_position(context.state, request.actor_id).get("location_id") != home
        ):
            raise ActionValidationError("NOT_AT_HOME")

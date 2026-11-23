"""Deterministic need/resource priority policy; input is only delivered Observation."""

import heapq
from dataclasses import dataclass, replace
from typing import cast

from journeymap.core.canonical import JsonObject, JsonValue, clone_json_object
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation, validate_observation_v1


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("invalid life Observation object")
    return value


def _int(value: JsonValue) -> int:
    if type(value) is not int:
        raise ValueError("invalid own resource value")
    return value


@dataclass(frozen=True, slots=True)
class LifeDecision:
    tick: int
    actor_id: str
    request_id: str
    action_type: str
    payload: JsonObject
    reason: str
    evidence: JsonObject


class LifeController:
    """Small state: observed closures and retry delays from public outcomes."""

    def __init__(self) -> None:
        self._blocked: set[str] = set()
        self._seen_receipt: str | None = None
        self._buy_retry_at = 0
        self._work_retry_at = 0
        self._decisions: list[LifeDecision] = []

    @property
    def decisions(self) -> tuple[LifeDecision, ...]:
        return tuple(
            replace(d, payload=clone_json_object(d.payload), evidence=clone_json_object(d.evidence))
            for d in self._decisions
        )

    @property
    def policy_state(self) -> JsonObject:
        return {
            "blocked_routes": cast(list[JsonValue], sorted(self._blocked)),
            "seen_receipt": self._seen_receipt,
            "buy_retry_at": self._buy_retry_at,
            "work_retry_at": self._work_retry_at,
        }

    def decide(self, observation: Observation) -> ActionRequest:
        validate_observation_v1(observation)
        sections = observation.content["sections"]
        assert isinstance(sections, list)
        content: JsonObject = {}
        for section in sections:
            content.update(_object(_object(section)["content"]))
        own = _object(content["survival"])
        inventory, trade, life = (_object(content[k]) for k in ("inventory", "trade", "life"))
        resident = _object(life["resident"])
        records = content["records"]
        assert isinstance(records, list)
        known_map = next(
            (
                _object(r)
                for r in records
                if _object(r).get("predicate") == "known_routes"
                and _object(r).get("actor_id") == observation.actor_id
            ),
            None,
        )
        if known_map is None or not isinstance(known_map["value"], list):
            raise ValueError("missing legitimately supplied initial map")
        taught = known_map["value"]
        position = _object(content["position"])
        location = position.get("location_id")
        hunger, fatigue = _int(own["hunger"]), _int(own["fatigue"])
        money, bread = _int(trade["wallet"]), _int(inventory.get("bread", 0))
        now = observation.simulation_time
        previous = content.get("last_action")
        if isinstance(previous, dict) and previous.get("status") in {
            "SUCCEEDED",
            "FAILED",
            "REJECTED",
        }:
            rid = str(previous["request_id"])
            if rid != self._seen_receipt:
                self._seen_receipt = rid
                failed = previous["status"] != "SUCCEEDED"
                if previous["action_type"] == "BUY":
                    self._buy_retry_at = now + 4 if failed else 0
                if previous["action_type"] == "WORK":
                    self._work_retry_at = now + 4 if failed else 0
                    if not failed:
                        self._buy_retry_at = 0
                if (
                    previous["action_type"] == "MOVE"
                    and previous["reason_code"] == "ROUTE_IMPASSABLE"
                ):
                    self._blocked.add(str(_object(previous["payload"])["route_id"]))
        local_routes = life["local_routes"]
        assert isinstance(local_routes, list)
        for row in local_routes:
            route = _object(row)
            if route["passable"] is False:
                self._blocked.add(str(route["route_id"]))
            else:
                self._blocked.discard(str(route["route_id"]))
        jobs, offers = life["local_jobs"], trade["offers"]
        assert isinstance(jobs, list) and isinstance(offers, list)
        available_job = next((_object(j) for j in jobs if _object(j)["available"] is True), None)
        food_offer = next((_object(o) for o in offers if _object(o)["item_id"] == "bread"), None)
        evidence: JsonObject = {
            "hunger": hunger,
            "fatigue": fatigue,
            "bread": bread,
            "wallet": money,
            "location": location,
            "resident": resident,
            "local_jobs": jobs,
            "local_offers": offers,
            "local_routes": local_routes,
            "previous_public_result": previous,
            "initial_map_record": known_map["knowledge_record_id"],
            "blocked_routes": cast(list[JsonValue], sorted(self._blocked)),
        }

        def choose(action: str, payload: JsonObject, reason: str) -> ActionRequest:
            request_id = observation.observation_id + ":life-v1"
            self._decisions.append(
                LifeDecision(
                    now,
                    observation.actor_id,
                    request_id,
                    action,
                    clone_json_object(payload),
                    reason,
                    clone_json_object(evidence),
                )
            )
            return ActionRequest(
                request_id,
                observation.run_id,
                observation.actor_id,
                observation.observation_id,
                now,
                action,
                1,
                payload,
            )

        def travel(destination: str, reason: str) -> ActionRequest:
            edges = [_object(r) for r in taught]
            if not isinstance(location, str):
                return choose("WAIT", {"duration": 1}, "IN_TRANSIT_NO_NEW_TASK")
            heap: list[tuple[int, str, str]] = [(0, location, "")]
            seen: set[str] = set()
            while heap:
                cost, place, first = heapq.heappop(heap)
                if place in seen:
                    continue
                seen.add(place)
                if place == destination and first:
                    return choose("MOVE", {"route_id": first}, reason)
                for edge in sorted(edges, key=lambda e: str(e["route_id"])):
                    if edge["origin"] == place and edge["route_id"] not in self._blocked:
                        heapq.heappush(
                            heap,
                            (
                                cost + _int(edge["traversal_cost"]),
                                str(edge["destination"]),
                                first or str(edge["route_id"]),
                            ),
                        )
            return choose("WAIT", {"duration": 2}, "NO_KNOWN_OPEN_PATH")

        def rest() -> ActionRequest:
            if location != resident["home"]:
                return travel(str(resident["home"]), "TRAVEL_FOR_REST")
            return choose("REST", {"duration": 6 if fatigue >= 45 else 4}, "RECOVER_FATIGUE")

        if hunger >= 30 and bread:
            return choose(
                "CONSUME", {"item_id": "bread", "quantity": min(2, bread)}, "EAT_OWN_FOOD"
            )
        if fatigue >= 55:
            return rest()
        if resident["role"] == "employer":
            if fatigue >= 20:
                return rest()
            return choose("WAIT", {"duration": 2}, "BUSINESS_OPEN")
        if bread <= 1 and money >= 2 and (hunger >= 20 or bread == 0) and now >= self._buy_retry_at:
            if location != resident["market"]:
                return travel(str(resident["market"]), "TRAVEL_FOR_FOOD")
            if food_offer is not None:
                price = _int(food_offer["unit_price"])
                quantity = min(5, money // price) if price else 5
                return choose(
                    "BUY",
                    {"offer_id": food_offer["offer_id"], "quantity": quantity},
                    "BUY_AFFORDABLE_FOOD",
                )
        needs_work = money < 10 or bread == 0
        if needs_work and now >= self._work_retry_at and hunger < 90 and fatigue < 80:
            if location != resident["job_location"]:
                return travel(str(resident["job_location"]), "TRAVEL_FOR_WORK")
            if available_job is not None:
                return choose("WORK", {"job_id": available_job["job_id"]}, "EARN_AND_PRODUCE")
        if fatigue >= 20:
            return rest()
        if bread == 0 and (money < 2 or now < self._buy_retry_at):
            return choose("WAIT", {"duration": 2}, "FOOD_OR_WORK_UNAVAILABLE_RECHECK")
        if needs_work:
            return choose("WAIT", {"duration": 2}, "JOB_UNAVAILABLE_RECHECK")
        return choose("WAIT", {"duration": 2}, "SATIATED_WAIT")

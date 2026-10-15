"""Three local map QA fixtures. Reverse closure is a test input, never a rule change."""

import selectors
from pathlib import Path

from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.examples.life_ui import create_server
from journeymap.experiments.life import LifeSession
from journeymap.experiments.live import LiveLife
from journeymap.scenarios.life import LifeConfig


def main() -> None:
    selector = selectors.DefaultSelector()
    sessions = []
    servers = []
    try:
        for port, closed in (
            (8785, ()),
            (8786, ("square-shop",)),
            (8787, ("square-shop", "shop-square")),
        ):
            session = LifeSession(LifeConfig())
            sessions.append(session)
            for route in closed:
                session.install(ScheduledEventSpec(0, 0, "CloseRoute", 1, {"route_id": route}))
            session.run_until(0)
            server = create_server(LiveLife(session, Path("artifacts/phase4/map-qa.json")), port)
            servers.append(server)
            selector.register(server, selectors.EVENT_READ)
            print(f"map fixture {closed}: http://127.0.0.1:{port}", flush=True)
        while True:
            for key, _ in selector.select(0.1):
                key.fileobj.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        selector.close()
        for server in servers:
            server.server_close()
        for session in sessions:
            session.close()


if __name__ == "__main__":
    main()

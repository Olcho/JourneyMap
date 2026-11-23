"""Persistence lifecycle port used by the simulation kernel."""

from typing import Protocol


class Persistence(Protocol):
    """Technology-neutral persistence lifecycle.

    Concrete schemas and transactional record operations are added by the
    milestone that introduces those records. The M0 kernel only owns lifecycle.
    """

    def initialize(self) -> None:
        """Prepare the persistence resource for a kernel run."""

    def close(self) -> None:
        """Release the persistence resource."""

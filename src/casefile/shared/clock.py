from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware timestamp; implementations control access to real time."""
        ...

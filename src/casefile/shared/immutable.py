"""Construction support for frozen dataclasses with validating public factories."""

from dataclasses import dataclass, fields
from typing import Self


@dataclass(frozen=True, slots=True, init=False)
class FactoryOnly:
    """Keep unchecked construction private; misuse is a programming error."""

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError(f"Use a validating factory to construct {cls.__name__}")

    @classmethod
    def _new(cls, **values: object) -> Self:
        if set(values) != {field.name for field in fields(cls)}:
            raise TypeError(f"Incorrect internal fields for {cls.__name__}")
        instance = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    def _evolve(self, **changes: object) -> Self:
        values: dict[str, object] = {
            field.name: getattr(self, field.name) for field in fields(self)
        }
        return self._new(**(values | changes))

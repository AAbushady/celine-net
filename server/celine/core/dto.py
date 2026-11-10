"""What a route answers, declared: the base of every answer model and the shapes more than
one route's answer is made of.

A route names its answer as a model (a DTO) in its return type. The model is the
contract: a field it does not name is refused when the answer is built, and the page's
types are written from the models (page_types.py), so a field renamed here fails the
page's build until the page follows. A feature's own models sit beside its routes."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from .statuses import Tone


class Answer(BaseModel):
    """The base of every answer model."""
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def of(cls, row: Mapping) -> Self:
        """This answer from a row that may hold more: the model's own fields, each of
        which the row must have."""
        return cls(**{name: row[name] for name in cls.model_fields})


class Display(Answer):
    """A status as the page shows it (core/statuses.py)."""
    words: str
    tone: Tone


class FilterOption(Answer):
    """One State option of a list: the value a row's `filter` holds, the label the page
    shows and how many of the rows sent fall under it."""
    value: str
    label: str
    count: int


# where a conversation lives (homes.py)
Home = Literal["agent", "missions", "routines"]


class ConversationCrumb(Answer):
    """One conversation of a path from the top down (homes.path)."""
    id: str
    kind: Literal["chat", "mission", "run", "background", "agent"]
    name: str
    link: str | None

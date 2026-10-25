"""Bounded input/output contracts shared by the MCP client and server."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class ReferenceArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=120)
    limit: int = Field(default=3, ge=1, le=5)


class ReferenceHit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: Literal['goal', 'review', 'meeting']
    title: str = Field(min_length=1, max_length=100)
    excerpt: str = Field(min_length=1, max_length=1000)
    source: str = Field(pattern=r'^familyagent://reference/(goal|review|meeting)$')


class ReferenceOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    hits: list[ReferenceHit] = Field(max_length=5)


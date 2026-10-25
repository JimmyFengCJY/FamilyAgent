from typing import Annotated, Literal
from pydantic import BaseModel as PydanticBaseModel, ConfigDict, Field, field_validator


class BaseModel(PydanticBaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class Register(Login):
    name: str = Field(min_length=1, max_length=30)
    invite: str | None = None


class CharacterInput(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    capability: Literal["host", "planner", "support", "finance", "assistant"]
    tone: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=300)
    color: Literal["sage", "lavender", "peach", "blue", "rose"] = "sage"


class ConversationInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    mode: Literal["meeting", "planning", "support", "finance", "assistant"]
    character_ids: list[str] = Field(default_factory=list, max_length=3)
    participants: list[str] = Field(default_factory=list, max_length=12)
    use_memory: bool = False
    orchestration: Literal["single", "multi", "independent"] = "independent"
    constraints: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=8)


class BudgetItem(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    amount: str = Field(max_length=20)


class FinanceInput(BaseModel):
    income: str = Field(max_length=20)
    items: list[BudgetItem] = Field(min_length=1, max_length=30)
    currency: Literal["CNY", "HKD", "USD"] = "CNY"


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    max_calls: int = Field(default=8, ge=1, le=12)
    finance: FinanceInput | None = None
    intent: Literal["auto", "question", "support", "advice", "planning", "task", "discussion"] = "auto"

    @field_validator("content")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入内容")
        return value.strip()


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    due_at: str | None = None


class TaskUpdate(BaseModel):
    status: Literal["todo", "done", "dismissed"]


class MemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class Acceptance(BaseModel):
    version: int = Field(ge=1)
    accepted: bool = True


class ResumeInput(BaseModel):
    max_calls: int | None = Field(default=None, ge=1, le=12)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


class Position(BaseModel):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=1000)


class Disagreement(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    positions: list[Position] = Field(min_length=2, max_length=3)
    question: str = Field(min_length=1, max_length=500)


class AgentResult(BaseModel):
    summary: str = Field(min_length=1, max_length=6000)
    proposals: list[str] = Field(default_factory=list, max_length=8)
    assumptions: list[str] = Field(default_factory=list, max_length=6)
    missing_information: list[str] = Field(default_factory=list, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=6)
    suggested_actions: list[Annotated[str, Field(min_length=1, max_length=150)]] = Field(default_factory=list, max_length=6)
    consensus: list[str] = Field(default_factory=list, max_length=6)
    disagreements: list[Disagreement] = Field(default_factory=list, max_length=6)

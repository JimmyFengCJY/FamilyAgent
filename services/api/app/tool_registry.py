"""Local tool contracts. Execution context is server-owned, never model arguments."""
import time
from dataclasses import dataclass
from typing import Annotated, Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .schemas import FinanceInput, BudgetItem
from .tools import calculate_budget
from .db import now, can_access
from .approvals import propose_hash
from .mcp_client import ReferenceArguments, search_reference, McpFailure

VERSION = 'local-tools-v2-mcp'


class ToolError(ValueError):
    pass


class ToolDenied(ToolError):
    pass


class ToolArgumentsError(ToolError):
    pass


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class TaskListArguments(Arguments):
    status: Literal['todo', 'done', 'dismissed'] | None = None
    limit: int = Field(default=30, ge=1, le=30)


class FinanceItem(BudgetItem):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class FinanceArguments(FinanceInput):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    items: list[FinanceItem] = Field(min_length=1, max_length=30)


class BatchArguments(Arguments):
    actions: list[Annotated[str, Field(min_length=1, max_length=150)]] = Field(min_length=1, max_length=6)


@dataclass(frozen=True)
class ToolContext:
    store: object
    conn: object
    user: dict
    origin: str
    conversation: dict | None = None
    run: dict | None = None
    capability: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    origins: tuple[str, ...]
    capabilities: tuple[str, ...]
    risk: str
    requires_approval: bool
    handler: Callable
    transport: str = 'local'

    def describe(self):
        return {'name': self.name, 'description': self.description, 'input_schema': self.arguments.model_json_schema(),
                'risk': self.risk, 'requires_approval': self.requires_approval, 'origins': list(self.origins),
                'capabilities': list(self.capabilities), 'version': VERSION, 'transport': self.transport}


def authorize(tool, ctx):
    if not ctx.user or ctx.origin not in tool.origins:
        raise ToolDenied('工具调用入口未获授权')
    if ctx.origin in {'model', 'budget'}:
        if not ctx.run or not ctx.conversation or not can_access(ctx.user, ctx.conversation):
            raise ToolDenied('无权访问工具所属会话')
        if ctx.run['owner_id'] != ctx.user['id'] or ctx.run['conversation_id'] != ctx.conversation['id']:
            raise ToolDenied('工具运行身份不匹配')
        if ctx.run.get('status') != 'running':
            raise ToolDenied('工具仅可在有效执行阶段调用')
        if ctx.capability not in tool.capabilities:
            raise ToolDenied('该角色不具备工具能力')
        if tool.name == 'task.list' and (ctx.conversation['scope'] != 'private' or ctx.conversation['mode'] == 'support'):
            raise ToolDenied('本空间不允许读取个人待办')
        if tool.name == 'finance.calculate' and ctx.conversation['mode'] != 'finance':
            raise ToolDenied('预算工具仅用于预算分析空间')
        if tool.name == 'reference.search' and ctx.conversation['mode'] == 'support':
            raise ToolDenied('情绪支持空间不开放参考资料工具')
    if tool.requires_approval:
        run = ctx.run or {}
        proposal = run.get('proposal', {})
        if (ctx.origin != 'approval' or run.get('owner_id') != ctx.user['id']
            or run.get('status') != 'waiting_approval' or proposal.get('kind') != 'tasks'
            or proposal.get('required_approvers') != [ctx.user['id']]
            or run.get('approvals', {}).get(ctx.user['id']) is not True
            or proposal.get('expires_at', 0) < time.time()):
            raise ToolDenied('创建任务需要接收人确认有效任务草案')
        if propose_hash(run) != proposal.get('hash'):
            raise ToolDenied('任务草案完整性检查失败')
        saved = ctx.store.get(run['id'], ctx.conn)
        if (not saved or saved.get('status') != 'waiting_approval' or saved.get('version') != run['version']
            or saved.get('proposal') != proposal or saved.get('approvals') != run.get('approvals')):
            raise ToolDenied('任务审批状态已经变化')
        conv = ctx.store.get(run['conversation_id'], ctx.conn)
        if not can_access(ctx.user, conv):
            raise ToolDenied('无权访问任务草案所属会话')


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError('工具名称重复')
        self._tools[tool.name] = tool

    def catalog(self, ctx=None):
        result = []
        for tool in self._tools.values():
            if ctx is not None:
                try:
                    authorize(tool, ctx)
                except ToolDenied:
                    continue
            result.append(tool.describe())
        return result

    def prepare(self, name, arguments, ctx):
        tool = self._tools.get(name)
        if tool is None:
            raise ToolDenied('工具未注册')
        # Authorization precedes argument validation; model data cannot set identity/origin.
        authorize(tool, ctx)
        try:
            data = tool.arguments.model_validate(arguments)
        except ValidationError:
            raise ToolArgumentsError('工具参数不符合 Schema；未知字段、类型或范围无效') from None
        if tool.requires_approval and data.actions != ctx.run['proposal']['actions']:
            raise ToolDenied('工具参数与已确认任务草案不一致')
        return tool, data

    def invoke_read(self, prepared, ctx):
        tool, data = prepared
        if ctx.origin != 'model' or ctx.conn is not None or tool.risk != 'L0' or tool.requires_approval:
            raise ToolDenied('事务外执行仅允许模型入口的只读工具')
        authorize(tool, ctx)
        return self._invoke(tool, data, ctx)

    def _invoke(self, tool, data, ctx):
        try:
            return tool.handler(data, ctx)
        except ToolError:
            raise
        except ValueError:
            raise ToolArgumentsError('工具输入数据无效，未完成执行') from None
        except Exception:
            raise ToolError('本地工具执行失败') from None

    def execute(self, name, arguments, ctx):
        tool, data = self.prepare(name, arguments, ctx)
        started = time.perf_counter()
        if ctx.run:
            ctx.store.event(ctx.run['id'], 'tool.started', {'name': name, 'risk': tool.risk, 'origin': ctx.origin, 'transport': tool.transport}, ctx.conn)
        result = self._invoke(tool, data, ctx)
        if ctx.run:
            ctx.store.event(ctx.run['id'], 'tool.completed', {'name': name, 'risk': tool.risk, 'origin': ctx.origin, 'transport': tool.transport,
                'elapsed_seconds': round(time.perf_counter() - started, 6),
                'count': len(result) if isinstance(result, list) else 1,
                **({'idempotency_key': ctx.run['id']} if tool.requires_approval else {})}, ctx.conn)
        return result


def list_tasks(args, ctx):
    return [{'title': task['title'], 'status': task['status']} for task in ctx.store.all('task', ctx.conn)
            if task['owner_id'] == ctx.user['id'] and (args.status is None or task['status'] == args.status)][:args.limit]


def create_tasks(args, ctx):
    ids = []
    for index, title in enumerate(args.actions):
        key = f"task_{ctx.run['id']}_{index}"
        ctx.store.put('task', {'id': key, 'owner_id': ctx.user['id'], 'title': title, 'status': 'todo', 'due_at': None,
            'source_run': ctx.run['id'], 'created_at': now(), 'notified': False}, ctx.conn)
        ids.append(key)
    return ids


registry = ToolRegistry()
registry.register(Tool('task.list', '读取当前用户待办，可筛选状态，最多30条', TaskListArguments,
    ('model',), ('planner', 'assistant'), 'L0', False, list_tasks))
registry.register(Tool('finance.calculate', '使用本次输入精确计算收支与结余，不提供市场行情', FinanceArguments,
    ('model', 'budget', 'api'), ('finance',), 'L0', False, lambda args, ctx: calculate_budget(args.model_dump())))
registry.register(Tool('task.create_batch', '确认任务草案后创建接收人的待办；模型不可直接调用', BatchArguments,
    ('approval',), (), 'L1', True, create_tasks))


def reference_search(args, ctx):
    try:
        return search_reference(args.model_dump())
    except McpFailure as exc:
        raise ToolError(str(exc)) from None


registry.register(Tool('reference.search', '通过本地 MCP stdio 检索内置的目标拆解、学习复盘与会议参考资料，不读取个人数据', ReferenceArguments,
    ('model', 'probe'), ('planner', 'assistant', 'host'), 'L0', False, reference_search, 'stdio'))

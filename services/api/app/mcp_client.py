"""Fixed-server MCP bridge using the official SDK; no user-supplied commands."""
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
import anyio
from services.mcp_reference.contracts import ReferenceArguments, ReferenceOutput

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL = '2025-11-25'


class McpFailure(Exception):
    pass


def server_parameters():
    from mcp import StdioServerParameters
    # SDK merges a small OS environment allowlist, not all of os.environ.
    return StdioServerParameters(command=sys.executable,
        args=[str(ROOT / 'services/mcp_reference/server.py')], cwd=str(ROOT / 'services/mcp_reference'), env={'PYTHONIOENCODING': 'utf-8'})


async def exchange(parameters, arguments, timeout=10):
    """Parameters injected only by internal tests; public callers use fixed server."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    try:
        with open(os.devnull, 'w') as errlog:
            with anyio.fail_after(timeout):
                async with stdio_client(parameters, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        initialized = await session.initialize()
                        if initialized.protocolVersion != PROTOCOL or initialized.capabilities.tools is None:
                            raise McpFailure('MCP 协议版本或工具能力不匹配')
                        discovered = await session.list_tools()
                        tools = {tool.name: tool for tool in discovered.tools}
                        if set(tools) != {'search_reference'} or discovered.nextCursor:
                            raise McpFailure('MCP 工具发现不符合内置服务合约')
                        tool = tools['search_reference']
                        schema = tool.inputSchema
                        if schema.get('type') != 'object' or set(schema.get('properties', {})) != {'query', 'limit'} or schema.get('required') != ['query']:
                            raise McpFailure('MCP 工具输入 Schema 不符合内置合约')
                        from jsonschema import Draft202012Validator
                        Draft202012Validator.check_schema(schema)
                        Draft202012Validator(schema).validate(arguments)
                        if not isinstance(tool.outputSchema, dict):
                            raise McpFailure('MCP 服务未声明输出 Schema')
                        Draft202012Validator.check_schema(tool.outputSchema)
                        result = await session.call_tool('search_reference', arguments)
                        if result.isError:
                            raise McpFailure('MCP 工具返回执行错误，未使用其内容')
                        data = result.structuredContent
                        if not isinstance(data, dict) or len(json.dumps(data, ensure_ascii=False).encode()) > 16000:
                            raise McpFailure('MCP 工具输出格式或大小不符合约定')
                        Draft202012Validator(tool.outputSchema).validate(data)
                        output = ReferenceOutput.model_validate(data)
                        if len(output.hits) > arguments['limit'] or len({hit.id for hit in output.hits}) != len(output.hits) or any(hit.source != 'familyagent://reference/' + hit.id for hit in output.hits):
                            raise McpFailure('MCP 结果来源或数量不符合约定')
                        return {**output.model_dump(), 'server': 'familyagent-reference',
                                'protocol': initialized.protocolVersion, 'transport': 'stdio',
                                'discovered_tools': sorted(tools), 'source_type': 'bundled-reference-not-personal-data'}
    except TimeoutError:
        raise McpFailure('MCP 调用超时，连接已结束；未自动重试') from None
    except Exception:
        # SDK task groups may wrap transport/validation errors. Never echo stderr,
        # server messages, commands, args or internal exception details.
        raise McpFailure('MCP 连接、协议或结果校验失败；未自动重试') from None


def search_reference(arguments):
    data = ReferenceArguments.model_validate(arguments).model_dump()
    return anyio.run(exchange, server_parameters(), data)

#!/usr/bin/env python3
import asyncio
import json
import os
import sys
from typing import Any, Dict
import argparse

# MCP Client imports
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


async def list_tools(session: ClientSession):
    """Lists all available tools from the MCP server."""
    tools_response = await session.list_tools()
    print("\n--- Available Tools ---")
    for i, tool in enumerate(tools_response.tools):
        print(f"[{i}] {tool.name}: {tool.description}")
    return tools_response.tools


async def call_tool(session: ClientSession, tool_name: str, arguments: Dict[str, Any]):
    """Calls a specific tool and prints the result."""
    print(
        f"\nCalling tool '{tool_name}' with arguments: {json.dumps(arguments, indent=2)}"
    )
    try:
        result = await session.call_tool(tool_name, arguments)
        print("\n--- Result ---")
        # result.content is a list of content blocks (TextContent, ImageContent, etc.)
        for block in result.content:
            if hasattr(block, "text"):
                # Try to parse as JSON if it looks like one
                try:
                    data = json.loads(block.text)
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                except:
                    print(block.text)
            else:
                print(block)
    except Exception as e:
        print(f"Error calling tool: {e}")


async def interactive_loop(session: ClientSession):
    """Main interactive loop to explore the MCP server."""
    while True:
        tools = await list_tools(session)
        print("\nCommands:")
        print("  [number]  - Run the corresponding tool")
        print("  q         - Quit")

        choice = input("\nEnter choice: ").strip().lower()
        if choice == "q":
            break

        try:
            idx = int(choice)
            if 0 <= idx < len(tools):
                tool = tools[idx]
                print(f"\nTool: {tool.name}")
                print(f"Schema: {json.dumps(tool.inputSchema, indent=2)}")

                args_input = input(
                    "\nEnter JSON arguments (or leave empty for {}): "
                ).strip()
                args = {}
                if args_input:
                    try:
                        args = json.loads(args_input)
                    except json.JSONDecodeError as e:
                        print(f"Invalid JSON: {e}")
                        continue

                await call_tool(session, tool.name, args)
                input("\nPress Enter to continue...")
            else:
                print("Invalid number.")
        except ValueError:
            print("Invalid input.")


async def main():
    parser = argparse.ArgumentParser(description="ODIS MCP Play Script")
    parser.add_argument(
        "--server",
        choices=["core", "ft"],
        default="core",
        help="MCP server to connect to (core or ft)",
    )
    parser.add_argument("--tool", help="Name of the tool to run")
    parser.add_argument("--args", help="JSON string of arguments for the tool")
    parser.add_argument("--list", action="store_true", help="Just list tools and exit")
    args = parser.parse_args()

    # Server configuration mapping
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    python_path = os.path.join(root_dir, ".venv", "bin", "python")

    server_mapping = {
        "core": os.path.join(root_dir, "app", "services", "mcp_server.py"),
        "ft": os.path.join(root_dir, "app", "services", "mcp_france_travail.py"),
    }

    server_script = server_mapping[args.server]

    server_env = {
        **os.environ,
        "PYTHONPATH": f"{os.environ.get('PYTHONPATH', '')}:{os.path.join(root_dir, 'app')}",
    }
    server_env["MCP_SIMPLE_LOGS"] = "true"

    server_params = StdioServerParameters(
        command=python_path, args=[server_script], env=server_env
    )

    print(f"Connecting to {args.server.upper()} MCP server: {server_script} ...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the session
            await session.initialize()

            if args.list:
                await list_tools(session)
            elif args.tool:
                tool_args = json.loads(args.args) if args.args else {}
                await call_tool(session, args.tool, tool_args)
            else:
                await interactive_loop(session)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)

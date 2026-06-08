#!/usr/bin/env python3
"""
AgriMeshAI — AI Agent for Smart Agriculture
Entry point: connects to Ollama, uses recorder for data, enables tool calling.
"""

import os
import sys
import json
import asyncio
import yaml
from openai import OpenAI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- Config ----
with open(os.path.join(ROOT, "config", "models.yaml")) as f:
    config = yaml.safe_load(f)

model_name = config["llm"]["model"]
api_url = config["llm"]["api_url"]
max_tokens = config["llm"].get("max_tokens", 4096)
temperature = config["llm"].get("temperature", 0.2)

# ---- Instructions ----
instructions_path = os.path.join(os.path.dirname(__file__), "instructions.txt")
with open(instructions_path, "r", encoding="utf-8") as f:
    system_instruction = f.read()


def get_openai_tools():
    """Get MCP fleet tools converted to OpenAI function-calling format."""
    from mcp_server.tools.fleet import get_fleet_tools

    openai_tools = []
    for tool in get_fleet_tools():
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            },
        })
    return openai_tools


async def run_agent():
    """Run the agent with Ollama and tool calling."""
    from recorder import Recorder, ReadingsStore
    from mcp_server.tools.fleet import handle_fleet_tool

    # Init recorder
    store = ReadingsStore(os.path.join(ROOT, "data", "agrimesh.db"))
    recorder = Recorder(store)
    await recorder.start()

    # Get tool definitions
    tools_openai = get_openai_tools()

    client = OpenAI(base_url=api_url, api_key="ollama")

    messages = [
        {"role": "system", "content": system_instruction},
    ]

    tool_names = [t["function"]["name"] for t in tools_openai]
    print(f"✓ Agent ready (model: {model_name})")
    print(f"  Tools: {', '.join(tool_names)}")
    print(f"  Type 'exit' to quit\n")

    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ("exit", "quit", "thoát"):
                print("Goodbye!")
                break

            messages.append({"role": "user", "content": user_input})

            # First call: detect tool_calls (non-streaming)
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools_openai,
                tool_choice="auto",
                max_tokens=max_tokens,
                temperature=temperature,
            )

            msg = response.choices[0].message

            if msg.tool_calls:
                # Execute tool calls
                messages.append(msg)
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    tool_args = json.loads(tc.function.arguments)
                    print(f"  🔧 {tool_name}({tool_args})")

                    result = await handle_fleet_tool(tool_name, tool_args, recorder)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, indent=2, default=str),
                    })

                # Second call: stream final answer with tool results
                stream = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                print("\nAgent: ", end="", flush=True)
                full = ""
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        print(chunk.choices[0].delta.content, end="", flush=True)
                        full += chunk.choices[0].delta.content
                print()
                messages.append({"role": "assistant", "content": full})
            else:
                # No tools — direct text response
                print(f"\nAgent: {msg.content}")
                messages.append(msg)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")

    await recorder.stop()


if __name__ == "__main__":
    asyncio.run(run_agent())

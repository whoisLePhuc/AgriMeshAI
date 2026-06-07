#!/usr/bin/env python3
"""
AgriMeshAI — AI Agent for Smart Agriculture
Entry point: starts the agent, connects to Ollama, begins chat.
"""

import os
import sys
import yaml
from openai import OpenAI

# ---- Config ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def run_agent():
    """Run the agent with Ollama via OpenAI-compatible API."""
    client = OpenAI(base_url=api_url, api_key="ollama")

    messages = [
        {"role": "system", "content": system_instruction},
    ]

    print(f"✓ Agent ready (model: {model_name})")
    print(f"  Type 'exit' to quit\n")

    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ("exit", "quit", "thoát"):
                print("Goodbye!")
                break

            messages.append({"role": "user", "content": user_input})

            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )

            print("\nAgent: ", end="", flush=True)
            full_response = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    full_response += content
            print()

            messages.append({"role": "assistant", "content": full_response})

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    run_agent()

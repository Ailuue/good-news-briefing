"""Throwaway script: verify /no_think actually suppresses reasoning tokens."""
import sys
import os
import pathlib

# Load .env so PC_HOST is available
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent / ".env")
except ImportError:
    pass

from openai import OpenAI

PC_HOST = os.environ.get("PC_HOST", "127.0.0.1")
BASE_URL = f"http://{PC_HOST}:1234/v1"
MODEL = "unsloth/qwen3.6-35b-a3b"

client = OpenAI(base_url=BASE_URL, api_key="lm-studio")

PUZZLE = "If all bloops are razzles and all razzles are lazzles, are all bloops lazzles? Answer in one word. /no_think"

print("Sending logic puzzle with /no_think + enable_thinking:false ...")
resp = client.chat.completions.create(
    model=MODEL,
    temperature=0,
    max_tokens=512,
    messages=[{"role": "user", "content": PUZZLE}],
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)

choice = resp.choices[0]
content = choice.message.content or ""
reasoning = getattr(choice.message, "reasoning_content", None) or ""

print(f"\nfinish_reason : {choice.finish_reason}")
print(f"content length: {len(content)} chars")
print(f"reasoning len : {len(reasoning)} chars")
print(f"<think> in content: {'<think>' in content}")
print(f"\n--- content ---\n{content[:500]}")
if reasoning:
    print(f"\n--- reasoning_content (first 200 chars) ---\n{reasoning[:200]}")

if "<think>" in content or reasoning:
    print("\nRESULT: thinking is NOT suppressed — model is still generating reasoning tokens")
    sys.exit(1)
else:
    print("\nRESULT: thinking appears suppressed — no reasoning tokens detected")

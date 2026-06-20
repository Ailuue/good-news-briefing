"""Isolate why the loaded model produces garbage in classify().

Gemma was emitting structurally valid JSON whose free-text `reason` field
degenerated into word-salad, while the grammar-constrained fields stayed fine.
That points at the model/quant or the prompt, not the sampler. This runs three
tests to localize the fault. Run it with the suspect model loaded in LM Studio.

Reading the results:
  T1 garbage  -> the model can't generate coherent free text at all: bad/broken
                 quant or wrong chat template. Fix that first (re-download, try a
                 higher-bit quant, check LM Studio's prompt template).
  T1 fine, T2 garbage, T3 fine -> the JSON-schema grammar is trapping the model
                 in the free-text field. Constrain `reason` (maxLength / enum) or
                 relax response_format to json_object.
  T2 and T3 both garbage but T1 fine -> the classify *prompt* is the trigger
                 (length/template). Compare with/without the enable_thinking kwarg.
"""
import re
import sys

from good_news import config
from good_news.prompts import CRITERIA, VERDICT_SCHEMA
from openai import OpenAI

client = OpenAI(base_url=config.BASE_URL, api_key="lm-studio")
MODEL = config.CHAT_MODEL

# A plain article that triggered the degeneration for you (edit to a real one).
SAMPLE = (
    "SOURCE: positive.news\n"
    "TITLE: Community restores a polluted river to life\n"
    "SUMMARY: Volunteers spent two years clearing a local river of waste; fish "
    "and birds have returned and the town now swims there again."
)

# A quick, reliable signal that text is degenerate without reading all of it.
def looks_degenerate(text: str) -> bool:
    if not text:
        return True
    # Split on whitespace AND glue chars (/ _ - .) so slash-joined loops like
    # "environment/environment/environment" register as repeats, not long words.
    chunks = [c for c in re.split(r"[\s/_.\-]+", text) if c]
    if len(chunks) > 20 and len(set(chunks)) / len(chunks) < 0.4:  # low variety
        return True
    return "(cont.)" in text


def show(label: str, content: str, finish: str) -> bool:
    bad = looks_degenerate(content)
    flag = "DEGENERATE" if bad else "ok"
    print(f"\n=== {label} -> {flag} (finish={finish}, {len(content)} chars) ===")
    print(content[:400] + ("..." if len(content) > 400 else ""))
    return bad


def call(messages, response_format=None, extra_body=None) -> tuple[str, str]:
    # Only include response_format when set: passing null makes LM Studio 400 with
    # "Cannot read properties of null (reading 'type')".
    kwargs: dict = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 512,
        "messages": messages,
        "extra_body": extra_body or {},
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    return (choice.message.content or ""), choice.finish_reason


print(f"Diagnosing: {MODEL}")

# T1: can the model write coherent free text at all? No grammar, no extra_body.
c, f = call([{"role": "user", "content":
             "In one sentence, say why a community cleaning up a polluted river is good news."}])
t1_bad = show("T1 plain free-text (no grammar)", c, f)

# T2: the real classify path -- json_schema grammar, no thinking kwargs.
c, f = call(
    [{"role": "system", "content": CRITERIA}, {"role": "user", "content": SAMPLE}],
    response_format={"type": "json_schema", "json_schema": VERDICT_SCHEMA},
)
t2_bad = show("T2 classify WITH json_schema grammar", c, f)

# T3: same prompt, but ask for JSON via instruction instead of a grammar. This
# LM Studio build only accepts response_format.type of 'json_schema' or 'text',
# so use 'text' (no grammar) and let the prompt request the JSON; verdict_json()
# in the real pipeline already extracts the object from free text.
c, f = call(
    [{"role": "system", "content": CRITERIA + "\nReturn ONLY a JSON object, nothing else."},
     {"role": "user", "content": SAMPLE}],
    response_format={"type": "text"},
)
t3_bad = show("T3 classify WITHOUT grammar (text mode)", c, f)

print("\n--- verdict ---")
if t1_bad:
    print("Model fails even plain free text -> quant/template is broken. "
          "Re-download or try a higher-bit quant; check the chat template.")
elif t2_bad and not t3_bad:
    print("Only the grammar path degenerates -> the json_schema grammar traps "
          "the model in `reason`. Constrain it (maxLength) or use json_object.")
elif t2_bad and t3_bad:
    print("Both classify paths degenerate but plain text is fine -> the classify "
          "prompt itself is the trigger (length/format).")
else:
    print("No degeneration reproduced here -> the failing inputs differ; rerun "
          "with a SAMPLE that actually failed in your eval.")
sys.exit(0)

"""One schema-forced OpenAI call. The model interprets; it never returns free text and never computes."""
import json
import os
import pathlib
import time

from .. import metering

ROOT = pathlib.Path(__file__).resolve().parents[2]
PASS1_MODEL = os.environ.get("EXTRACT_MODEL_1", "gpt-5.4-mini")
PASS2_MODEL = os.environ.get("EXTRACT_MODEL_2", "gpt-5.5")


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def call_structured(model, system, user, schema, effort="none", name="submit_extraction"):
    """Returns (parsed_arguments, usage_dict). tool_choice forces the function, so the reply is always schema-shaped JSON."""
    _load_env()
    from openai import OpenAI
    t0 = time.perf_counter()
    r = OpenAI().responses.create(
        model=model, reasoning={"effort": effort}, max_output_tokens=60000,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=[{"type": "function", "name": name, "strict": True, "parameters": schema}],
        tool_choice={"type": "function", "name": name})
    metering.record("openai", model, r.usage.input_tokens, r.usage.output_tokens, time.perf_counter() - t0, effort=effort,
                    cached=getattr(getattr(r.usage, "input_tokens_details", None), "cached_tokens", 0))
    call = next(o for o in r.output if o.type == "function_call")
    return json.loads(call.arguments), {"model": model, "effort": effort, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}

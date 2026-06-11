"""moni.llm.shell_call — CLI wrapper for worker.sh to call LLM via call_llm().

Usage:
    python3 moni/llm/shell_call.py --prompt-file /tmp/prompt.txt [--provider X] [--model Y]
    echo "prompt" | python3 moni/llm/shell_call.py [--provider X] [--model Y]

Prints the LLM reply to stdout.  Exit code 0 on success, 1 on failure.
Logs token usage with module='share-content'.
"""
from __future__ import annotations

import argparse
import sys

from moni.llm.client import call_llm


def _resolve_default() -> tuple[str, str]:
    """Resolve default provider/model from DB."""
    try:
        from moni.db.connection import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT value FROM llm_settings WHERE key='active_summary_model_id'"
        ).fetchone()
        if row:
            model_id = row["value"]
            model_row = conn.execute(
                "SELECT model_id, provider_id FROM llm_models WHERE id=?",
                (model_id,),
            ).fetchone()
            if model_row:
                provider_row = conn.execute(
                    "SELECT name FROM llm_providers WHERE id=?",
                    (model_row["provider_id"],),
                ).fetchone()
                if provider_row:
                    return provider_row["hermes_provider"], model_row["model_id"]
    except Exception:
        pass
    return "google", "gemini-2.5-flash"


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM call wrapper for worker")
    parser.add_argument("--prompt-file", help="File containing the prompt")
    parser.add_argument("--provider", default="", help="Provider override")
    parser.add_argument("--model", default="", help="Model override")
    parser.add_argument("--module", default="share-content", help="Module tag for logging")
    args = parser.parse_args()

    # Read prompt
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    else:
        prompt = sys.stdin.read()

    if not prompt.strip():
        print("Error: empty prompt", file=sys.stderr)
        sys.exit(1)

    # Resolve provider/model
    provider = args.provider
    model = args.model
    if not provider or not model:
        default_prov, default_mod = _resolve_default()
        provider = provider or default_prov
        model = model or default_mod

    # Call LLM
    try:
        reply = call_llm(provider, model, prompt, module=args.module)
        print(reply)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

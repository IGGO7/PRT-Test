"""Spike D23 — executa o agente REAL sobre os quatro exemplos e um Beta editado, pelo mesmo caminho da interface.

Mostra, por cenário: campos extraídos (valor, origem, verificação exigida), evidências localizadas e sinais.
Uso:  python scripts/smoke_llm.py [beta gama alfa nutrivida edited]
"""

from __future__ import annotations

import sys
import time

from _env import load_env

load_env()

from vitalis.config import get_settings  # noqa: E402
from vitalis.dataset import get_example  # noqa: E402
from vitalis.extraction import OpenAIAgentExtractor  # noqa: E402
from vitalis.service import Service  # noqa: E402
from vitalis.store import MemoryStore  # noqa: E402


def main() -> None:
    s = get_settings()
    if not s.openai_api_key:
        raise SystemExit("Defina OPENAI_API_KEY.")
    svc = Service(MemoryStore(), s, lambda: OpenAIAgentExtractor(s.openai_api_key, s.openai_model, s.openai_timeout_s, s.openai_max_retries))
    sid, _ = svc.state(None)
    for t in sys.argv[1:] or ["beta", "gama", "alfa", "nutrivida", "edited"]:
        if t == "edited":
            fx = get_example("beta")
            body = {"mode": "manual_simulation", "sender": fx.sender, "subject": fx.subject,
                    "body": fx.body.replace("13% de desconto", "15% de desconto"), "derived_from_example": "beta"}
        else:
            body = {"mode": "dataset_example", "example_id": t}
        _, v = svc.receive(sid, body)
        started = time.monotonic()
        _, v = svc.analyze(sid, {"item_id": v["inbox"][0]["id"]})
        p = v["proposal"]
        print(f"\n=== {t} · {time.monotonic() - started:.1f}s · {p['extractor']}")
        for f in p["fields"]:
            print(f"  {f['key']:13} {str(f['value'])[:45]:45} [{f['origin_kind']:15}] {f['confirmation_status']:12} {f.get('reason') or ''}")
        print(f"  evidências no corpo: {len(p['evidence'])} · células do anexo: {p['csv_highlights']} · sinais: {list(p['flags'])}")
        print(f"  resumo da IA: {p.get('ai_summary')}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
download_revista_ateista.py

Baixa **todos** os artigos e autores da *Revista Ateísta* no Bubble e salva em
CSV, enriquecendo cada artigo com as informações do autor.

Correções importantes
--------------------
1. Campo **autor** pode vir como lista (`["id"]`), string simples ou objeto –
   agora tratamos todos os formatos.
2. Caso **não haja edições**, o script continua sem falhar (apenas avisa).
3. Adicionado `--debug` para imprimir a primeira página de cada fetch.

Arquivos gerados
----------------
• `edicoes_<timestamp>.csv` – pode vir vazio.
• `artigos_<timestamp>.csv` – bruto da API.
• `users_<timestamp>.csv`   – autores (nome, foto, bio).
• `artigos_enriched_<timestamp>.csv` – artigos + autor + status_display.

Uso
---
```bash
pip install requests python-dotenv
export BUBBLE_TOKEN=seu_token_privado
python download_revista_ateista.py --out data/
```

Adicione `--debug` para examinar o JSON cru.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

load_dotenv()

BASE_URL_DEFAULT = os.getenv("BUBBLE_URL")
DEFAULT_LIMIT = 100

ARTIGOS_TABLE = "artigos_revista_ateista"
EDICOES_TABLE = "edicao_revista_ateista"
USERS_TABLE   = "User"

# ---------------------------------------------------------------------------
# Funções de utilidade
# ---------------------------------------------------------------------------

def fetch_all(
    data_type: str,
    token: str,
    *,
    base_url: str = BASE_URL_DEFAULT,
    limit: int = DEFAULT_LIMIT,
    selected_fields: Sequence[str] | None = None,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """Faz paginação completa na API Bubble."""
    cursor = 0
    items: list[dict[str, Any]] = []

    common_params: dict[str, Any] = {"limit": limit}
    if selected_fields:
        common_params["properties"] = ",".join(selected_fields)

    while True:
        resp = requests.get(
            f"{base_url}/{data_type}",
            headers={"Authorization": f"Bearer {token}"},
            params=common_params | {"cursor": cursor},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()["response"]
        if debug and cursor == 0:
            print("\n―― DEBUG primeira página de", data_type)
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000], "…\n")
        items.extend(payload["results"])
        if payload.get("remaining", 0) == 0:
            break
        cursor += limit
    return items


def write_csv(rows: List[Dict[str, Any]], path: Path, *, bom: bool = True) -> None:
    """Salva lista de dicionários em CSV.

    * Usa UTF‑8 + BOM (utf‑8‑sig) por padrão, porque o Excel/LibreOffice no
      Windows identifica o encoding corretamente.  Se não quiser o BOM — por
      exemplo, para consumo em pipeline Unix — chame com `bom=False`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.touch()
        return

    headers = sorted({k for r in rows for k in r})
    encoding = "utf-8-sig" if bom else "utf-8"

    with path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def clean_id(value: Any) -> str | None:
    """Normaliza diferentes formatos de ID (str, list, json-string, obj)."""
    if not value:
        return None
    # Caso seja list já decodificada
    if isinstance(value, list):
        return clean_id(value[0]) if value else None
    # Caso venha como string parecendo lista ["..."]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list) and parsed:
                    return clean_id(parsed[0])
            except (SyntaxError, ValueError):
                pass
        return s
    # Caso seja objeto Bubble
    if isinstance(value, dict):
        return value.get("unique_id") or value.get("_id") or value.get("id")
    # Fallback
    return str(value)


def extract_display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display") or value.get("text") or value.get("label") or value
    return value


def enrich_articles(arts: List[Dict[str, Any]], users: List[Dict[str, Any]]) -> None:
    user_map = {u["_id"]: u for u in users}
    for art in arts:
        uid = clean_id(art.get("autor") or art.get("autor_id"))
        autor = user_map.get(uid, {})
        art["autor_nome"] = autor.get("nome")
        art["autor_bio"]  = autor.get("bio")
        art["autor_foto"] = autor.get("foto")
        art["status_display"] = extract_display(art.get("status"))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Baixa todos os artigos/usuários da Revista Ateísta em CSV")
    p.add_argument("--token", help="Token Bubble (fallback BUBBLE_TOKEN)")
    p.add_argument("--base-url", default=BASE_URL_DEFAULT)
    p.add_argument("--out", default="output")
    p.add_argument("--debug", action="store_true", help="Mostra JSON da primeira página de cada tabela")
    args = p.parse_args()

    token = args.token or os.getenv("BUBBLE_TOKEN")
    if not token:
        raise SystemExit("❌ Defina BUBBLE_TOKEN ou use --token.")

    out = Path(args.out)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    # Edições (pode vir vazio)
    print("🔽 Baixando edições …")
    edicoes = fetch_all(EDICOES_TABLE, token, base_url=args.base_url, debug=args.debug)
    write_csv(edicoes, out / f"edicoes_{ts}.csv")
    print(f"   → {len(edicoes)} edições salvas")

    # Artigos
    print("🔽 Baixando artigos …")
    artigos = fetch_all(ARTIGOS_TABLE, token, base_url=args.base_url, debug=args.debug)
    write_csv(artigos, out / f"artigos_{ts}.csv")
    print(f"   → {len(artigos)} artigos salvos")

    # Usuários
    print("🔽 Baixando usuários …")
    users = fetch_all(USERS_TABLE, token, base_url=args.base_url, selected_fields=["nome", "foto", "bio"], debug=args.debug)
    write_csv(users, out / f"users_{ts}.csv")
    print(f"   → {len(users)} usuários salvos")

    # Enriquecer
    enrich_articles(artigos, users)
    write_csv(artigos, out / f"artigos_enriched_{ts}.csv")
    print("✅ CSV enriquecido gerado!")


if __name__ == "__main__":
    main()

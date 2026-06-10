"""
backend/auth.py
───────────────
Autenticação simples por token (API_KEY) para os endpoints de escrita.

O cliente envia o header `X-API-Key`. A chave esperada vem da variável de
ambiente `API_KEY` (definida no `.env`).

Comportamento:
  - Se `API_KEY` NÃO estiver definida no ambiente, a autenticação fica
    desabilitada (modo aberto). Isso é intencional para não travar o uso em
    desenvolvimento, mas é registrado como aviso na inicialização (ver main.py).
  - Se `API_KEY` estiver definida, todo POST/PUT/DELETE protegido exige o header
    `X-API-Key` com o valor exato; caso contrário, responde 401.
"""

import os
import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


def auth_enabled() -> bool:
    """True quando uma API_KEY está configurada no ambiente."""
    return bool(os.getenv("API_KEY"))


async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Dependência FastAPI: valida o header `X-API-Key` contra a env `API_KEY`.

    Usa comparação em tempo constante (hmac.compare_digest) para evitar timing
    attacks. Quando `API_KEY` não está definida, libera o acesso.
    """
    expected = os.getenv("API_KEY")
    if not expected:
        # Auth desabilitada — nenhum segredo configurado.
        return

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=401,
            detail="Chave de API ausente ou inválida. Envie o header 'X-API-Key'.",
        )

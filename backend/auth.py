"""Perfil de acesso decidido no servidor, a partir de um token assinado (HS256).

Antes, o corpo de `/api/chat` dizia `access_level: "restrito"` e o servidor obedecia: qualquer
cliente lia o conteúdo restrito. Agora o perfil vem de um token com claims verificadas aqui:

    {"sub": <quem>, "tid": <CLIENT_ID>, "lvl": "publico"|"restrito", "iat": ..., "exp": ...}

- Sem token: só `publico`. O `access_level` do corpo só pode *reduzir* o que o token concede.
- Token com assinatura inválida, expirado ou de outro tenant (`tid` != CLIENT_ID): 401.
- Emissor de demo (`POST /api/session`): existe para a PoC mostrar o filtro de ACL sem um IdP.
  `RAG_DEMO_LOGIN=0` desliga o emissor; em produção o token vem do IdP (SSO) e só a
  verificação abaixo fica. Ver docs/adr/0001-perfil-de-acesso-no-servidor.md.

Segredo: `RAG_AUTH_SECRET`. Sem ele, cada processo sorteia um segredo efêmero (tokens morrem
no restart; nunca há segredo padrão conhecido).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

LEVELS = ("publico", "restrito")
_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
TOKEN_TTL_S = max(60, int(os.getenv("RAG_AUTH_TOKEN_TTL_S", "28800")))
DEMO_LOGIN = os.getenv("RAG_DEMO_LOGIN", "1") != "0"
_SECRET = (os.getenv("RAG_AUTH_SECRET") or secrets.token_hex(32)).encode()


class AuthError(Exception):
    """Token presente mas inválido: o chamador responde 401, nunca rebaixa em silêncio."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: bytes) -> str:
    return _b64(hmac.new(_SECRET, signing_input, hashlib.sha256).digest())


def issue_token(tenant_id: str, level: str, subject: str = "demo", ttl_s: int | None = None) -> str:
    if level not in _RANK:
        raise ValueError(f"nível desconhecido: {level}")
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"sub": subject, "tid": tenant_id, "lvl": level, "iat": now, "exp": now + (ttl_s or TOKEN_TTL_S)}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_sign(signing_input)}"


def verify_token(token: str, tenant_id: str) -> dict:
    """Claims do token, ou AuthError. Confere alg, assinatura, expiração, tenant e nível."""
    try:
        header_b64, payload_b64, sig = token.split(".")
        header = json.loads(_unb64(header_b64))
        claims = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise AuthError("token malformado") from e
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthError("algoritmo não aceito")  # barra alg=none e troca de algoritmo
    if not hmac.compare_digest(_sign(f"{header_b64}.{payload_b64}".encode()), sig):
        raise AuthError("assinatura inválida")
    if not isinstance(claims, dict):
        raise AuthError("claims inválidas")
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < time.time():
        raise AuthError("token expirado")
    if claims.get("tid") != tenant_id:
        raise AuthError("token de outro tenant")
    if claims.get("lvl") not in _RANK:
        raise AuthError("nível inválido")
    return claims


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("esperado 'Authorization: Bearer <token>'")
    return value.strip()


def granted_level(authorization: str | None, tenant_id: str) -> str:
    """Nível concedido pela requisição: `publico` sem token; o do token quando válido."""
    token = bearer(authorization)
    if token is None:
        return "publico"
    return verify_token(token, tenant_id)["lvl"]


def effective_level(granted: str, requested: str | None) -> str:
    """O pedido do cliente só reduz o concedido; nunca amplia."""
    if requested not in _RANK:
        return granted
    return requested if _RANK[requested] <= _RANK[granted] else granted


def levels_for(level: str) -> list[str]:
    """Níveis legíveis por um perfil (default-deny: valor desconhecido = só público)."""
    return list(LEVELS[: _RANK[level] + 1]) if level in _RANK else ["publico"]


def can_read(granted: str, required: str | None) -> bool:
    """Conversa/documento marcado `required` é legível por quem tem `granted`.

    Sem marca (dado legado) exige o nível mais alto: falha fechada.
    """
    need = required if required in _RANK else LEVELS[-1]
    return _RANK.get(granted, 0) >= _RANK[need]

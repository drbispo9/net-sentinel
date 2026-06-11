# 🚀 Guia de Deploy em Produção — NetSentinel

Este documento mostra **exatamente** como ficará o seu `.env` ao subir o sistema
em outro local e **o que você precisa alterar**. O backend roda direto no host
(Python) e serve também o frontend; por padrão usa **SQLite** (banco em arquivo,
sem servidor separado).

---

## 1. Pré-requisitos no servidor de produção

- Python 3.10+ instalado (`python --version`).
- Os arquivos do projeto (clone do repositório). O `.env` **não** vem no clone
  (está no `.gitignore`) — você cria ele no servidor, como mostrado abaixo.

---

## 2. O mínimo obrigatório

Com o padrão (SQLite), **nada é obrigatório** no `.env` para o sistema subir — o
banco em arquivo é criado automaticamente no primeiro start. Tudo o que segue é
opcional (segurança, integrações, ajustes de monitoramento).

---

## 3. O `.env` de produção (modelo completo)

Crie um arquivo `.env` na raiz do projeto com o conteúdo abaixo.
🟡 = **opcional** (só se quiser o recurso) &nbsp;|&nbsp; 🟢 = pode manter.

```dotenv
# ── Banco de dados ──────────────────────────────────────────────
# SQLite (padrão): banco em arquivo local, sem servidor.
DATABASE_URL=sqlite+aiosqlite:///./netsentinel.db
# PostgreSQL (opcional): aponte para um Postgres rodando no host.
# DATABASE_URL=postgresql+asyncpg://netsentinel:<senha-forte>@localhost:5432/netsentinel

# ── Segurança ───────────────────────────────────────────────────
# 🟡 OPCIONAL — protege as escritas (criar/editar/excluir).
#   VAZIA  = escrita ABERTA (qualquer um na rede edita). É assim que está.
#   COM valor = exige o header X-API-Key; informe a MESMA chave no frontend.
API_KEY=

# Origens permitidas no CORS. "*" libera todas (seguro: auth é por header).
CORS_ORIGINS=*

# ── Monitoramento de banco (locks) — opcionais ─────────────────
DB_MONITOR_INTERVAL_SECONDS=30                          # 🟢
DB_LOCK_MIN_WAIT_MS=0                                   # 🟢
DB_DATA_MAX_AGE_SECONDS=0                               # 🟢

# ── Portal OAB — check L7 autenticado (opcional) ───────────────
PORTAL_OAB_AUTH_URL=https://appws.oabgo.org.br/wsapp/wsapp/authenticate   # 🟢
PORTAL_OAB_USERNAME=seu_usuario_oab                     # 🟡 se usar o check OAB
PORTAL_OAB_PASSWORD=sua_senha_oab                       # 🟡 se usar o check OAB
```

---

## 4. O que alterar (resumo)

| Campo | Alterar? | O que colocar |
|-------|:-------:|---------------|
| `DATABASE_URL` | 🟢 Não (padrão SQLite) | Mantenha o SQLite, ou aponte para um PostgreSQL no host se preferir. |
| `API_KEY` | 🟡 Opcional | Deixe **vazia** para escrita aberta (sua escolha atual). Defina uma chave forte só se quiser exigir senha para criar/editar/excluir. |
| `PORTAL_OAB_USERNAME` / `PORTAL_OAB_PASSWORD` | 🟡 Opcional | Só se usar o check L7 autenticado da OAB. |
| `CORS_ORIGINS`, `DB_*`, `PORTAL_OAB_AUTH_URL` | 🟢 Não | Padrões bons. |

### Como gerar uma API_KEY forte

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. Subir o sistema

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env             # edite se quiser; o padrão já funciona
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Acesse em **http://SEU_SERVIDOR:8000**.

> Para rodar como serviço permanente, coloque o comando do `uvicorn` sob um
> gerenciador de processos do sistema (ex.: `systemd` no Linux, ou Tarefa
> Agendada / NSSM no Windows) apontando para o `python` do `venv`.

---

## 6. Pontos de atenção

- **O banco SQLite é um arquivo** (`netsentinel.db`) na raiz do projeto. Faça
  backup desse arquivo para preservar os dados; apagá-lo zera o sistema.

- **Produção começa com banco VAZIO** se o `netsentinel.db` não existir — ele é
  criado no primeiro start. Para levar dados de outra instância, copie o arquivo
  `.db` junto.

- **API_KEY e o frontend.** Ao definir `API_KEY`, o backend passa a exigir o
  header `X-API-Key` nas escritas. Você informa essa mesma chave na interface do
  frontend; sem isso, criar/editar/excluir para de funcionar.

- **O `.env` nunca vai para o git.** Ele fica só no servidor. O `.env.example`
  (versionado) serve de modelo: `cp .env.example .env` e edite.

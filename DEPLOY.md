# 🚀 Guia de Deploy em Produção — NetSentinel

Este documento mostra **exatamente** como ficará o seu `.env` ao subir o sistema
em outro local e **o que você precisa alterar**. O stack inteiro (backend +
frontend + PostgreSQL) sobe em containers Docker com um único comando.

---

## 1. Pré-requisitos no servidor de produção

- Docker + Docker Compose instalados (`docker compose version`).
- Os arquivos do projeto (clone do repositório). O `.env` **não** vem no clone
  (está no `.gitignore`) — você cria ele no servidor, como mostrado abaixo.

---

## 2. O mínimo obrigatório

De tudo no `.env`, **só uma coisa é obrigatória** para o sistema subir:

```dotenv
POSTGRES_PASSWORD=<uma-senha-forte>     # ← OBRIGATÓRIA
```

Se ela faltar, o `docker compose up` **falha de propósito** com a mensagem
*"defina POSTGRES_PASSWORD no .env"*. Todo o resto tem padrão ou é opcional.

---

## 3. O `.env` de produção (modelo completo)

Crie um arquivo `.env` na raiz do projeto com o conteúdo abaixo.
🔴 = **obrigatório** &nbsp;|&nbsp; 🟡 = **opcional** (só se quiser o recurso) &nbsp;|&nbsp; 🟢 = pode manter.

```dotenv
# ── Banco de dados ──────────────────────────────────────────────
# Rodando via Docker Compose, edite SOMENTE as 3 linhas abaixo.
POSTGRES_USER=netsentinel
# 🔴 OBRIGATÓRIA — gere a SUA (ver seção 4).
POSTGRES_PASSWORD=egfcktbdiA0F9rXYkGKXZMFuz9pM6z0y
POSTGRES_DB=netsentinel

# Usado só se rodar o backend FORA do Docker. Via Compose é IGNORADA.
# (Se for usar no host, repita aqui a mesma senha do POSTGRES_PASSWORD.)
DATABASE_URL=postgresql+asyncpg://netsentinel:egfcktbdiA0F9rXYkGKXZMFuz9pM6z0y@localhost:5432/netsentinel

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

> ⚠️ A senha `egfck...` acima é **apenas exemplo de formato**. NÃO a use em
> produção — gere a sua (seção 4).

---

## 4. O que alterar (resumo)

| Campo | Alterar? | O que colocar |
|-------|:-------:|---------------|
| `POSTGRES_PASSWORD` | 🔴 **Obrigatório** | Uma senha forte e única (32 caracteres). Sem ela o sistema não sobe. |
| `API_KEY` | 🟡 Opcional | Deixe **vazia** para escrita aberta (sua escolha atual). Defina uma chave forte só se quiser exigir senha para criar/editar/excluir. |
| `PORTAL_OAB_USERNAME` / `PORTAL_OAB_PASSWORD` | 🟡 Opcional | Só se usar o check L7 autenticado da OAB. |
| `DATABASE_URL` | 🟡 Só se rodar no host | Via Docker é ignorado (o Compose monta com host `postgres`). Se mexer no `POSTGRES_PASSWORD`, repita a senha aqui também. |
| `POSTGRES_USER` / `POSTGRES_DB` | 🟢 Não | Padrões funcionam. |
| `CORS_ORIGINS`, `DB_*`, `PORTAL_OAB_AUTH_URL` | 🟢 Não | Padrões bons. |

### Como gerar valores fortes

```bash
# Senha do banco (alfanumérica, sem caracteres que atrapalham a URL):
python -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"

# API_KEY:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. Subir o sistema

```bash
docker compose up -d --build
```

Acesse em **http://SEU_SERVIDOR:8000**.

| Ação | Comando |
|------|---------|
| Ver logs | `docker compose logs -f backend` |
| Parar (mantém dados) | `docker compose down` |
| Parar e **apagar** dados | `docker compose down -v` |
| Atualizar após mudar código | `docker compose up -d --build` |

---

## 6. Pontos de atenção

- **A senha do banco só "pega" na primeira subida** (volume novo). Em produção
  nova, isso é o seu caso → funciona limpo. Se um dia precisar trocar a senha com
  o banco já existente, é via `ALTER USER` (não basta editar o `.env`).

- **Produção começa com banco VAZIO.** Um volume novo não tem dados. Se quiser
  levar os dados atuais, é um passo separado (dump/restore ou a migração
  `backend/migrate_sqlite_to_postgres.py`).

- **API_KEY e o frontend.** Ao definir `API_KEY`, o backend passa a exigir o
  header `X-API-Key` nas escritas. Você informa essa mesma chave na interface do
  frontend; sem isso, criar/editar/excluir para de funcionar.

- **O `.env` nunca vai para o git.** Ele fica só no servidor. O `.env.example`
  (versionado) serve de modelo: `cp .env.example .env` e edite.

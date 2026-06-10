# 🛡️ NetSentinel

**Painel de monitoramento distribuído de dispositivos em tempo real.**

Sistema completo para monitorar dispositivos Web (HTTP/HTTPS) e Hardware (IP/Ping) com alertas sonoros, notificações em tempo real via WebSocket e interface moderna.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Async-4169E1?logo=postgresql&logoColor=white)

---

## ✨ Funcionalidades

- 🌐 **Monitoramento Web** — Verificação automática de URLs a cada 30s com retry
- 🖥️ **Infraestrutura Hardware** — Monitoramento de dispositivos físicos via workers
- 🔔 **Alertas em tempo real** — WebSocket para notificações instantâneas
- 🔇 **Silenciar individual** — Controle de alerta por dispositivo
- ✏️ **Editar dispositivos** — Alterar nome, tipo e endereço
- 📊 **Detalhes e histórico** — Uptime estimado e log de eventos
- 🎨 **Interface premium** — Design moderno com glassmorphism e animações

## 📁 Estrutura do Projeto

```
Monitotamento/
├── backend/
│   ├── main.py          # API FastAPI (rotas, WebSocket, CORS)
│   ├── models.py        # Modelos SQLAlchemy (Device, EventLog)
│   ├── schemas.py       # Schemas Pydantic (validação)
│   ├── database.py      # Engine async (PostgreSQL/SQLite) + pool
│   ├── monitor.py       # Loop de monitoramento assíncrono
│   └── init_db.py       # Inicialização do banco
├── frontend/
│   ├── index.html       # Dashboard principal
│   ├── app.js           # Lógica do frontend (REST + WebSocket)
│   ├── styles.css       # Estilos premium (dark mode, glassmorphism)
│   └── alerta_critico.wav
├── worker/              # Workers para monitoramento de hardware
├── .env.example         # Exemplo de variáveis de ambiente
├── requirements.txt     # Dependências Python
└── README.md
```

## 🚀 Como Rodar

Há dois caminhos: **(A) tudo em Docker** (recomendado — não precisa de Python
nem dependências na máquina) ou **(B) backend no host** com o Postgres em Docker.

---

## 🐳 Opção A — Tudo em Docker (recomendado)

Sobe **backend (API + frontend) + PostgreSQL** com um comando.

```bash
git clone https://github.com/drbispo9/net-sentinel.git
cd net-sentinel

cp .env.example .env          # edite e defina ao menos POSTGRES_PASSWORD
docker compose up -d --build  # builda a imagem e sobe todo o stack
```

Acesse **http://localhost:8000**. As tabelas são criadas automaticamente no
primeiro start.

| Ação | Comando |
|---|---|
| Ver logs do backend | `docker compose logs -f backend` |
| Parar (mantém dados) | `docker compose down` |
| Parar e **apagar** dados | `docker compose down -v` |
| Rebuildar após mudar código | `docker compose up -d --build` |

> O serviço `backend` recebe as variáveis do `.env` (via `env_file`) e usa o host
> `postgres` (nome do serviço) para falar com o banco. O `ping` do monitor de
> hardware funciona graças à capability `NET_RAW` declarada no compose.
>
> Já tem um `netsentinel.db` (SQLite) com dados? Veja como importá-lo na seção
> **Opção B** abaixo (a migração roda a partir do host, onde o arquivo `.db` está).

---

## 💻 Opção B — Backend no host (Postgres em Docker)

### 1. Clone o repositório
```bash
git clone https://github.com/drbispo9/net-sentinel.git
cd net-sentinel
```

### 2. Crie o ambiente virtual
```bash
python -m venv venv
venv\Scripts\activate     # Windows
# ou
source venv/bin/activate  # Linux/Mac
```

### 3. Instale as dependências
```bash
pip install -r requirements.txt
```

### 4. Configure o ambiente
```bash
copy .env.example .env
# Edite o .env com suas configurações
```

### 4.1. Suba o banco PostgreSQL (recomendado)
```bash
docker compose up -d        # sobe o Postgres com volume persistente
```
O `.env.example` já vem apontando para esse banco:
`postgresql+asyncpg://netsentinel:netsentinel@localhost:5432/netsentinel`.
As tabelas são criadas automaticamente na primeira inicialização do servidor.

> Sem Docker? Use o fallback SQLite descomentando a linha `sqlite+aiosqlite://...`
> no `.env` — não requer servidor, mas não suporta concorrência tão bem.

**Migrar dados de um SQLite existente para o Postgres** (opcional):
```bash
python -m backend.migrate_sqlite_to_postgres \
  --sqlite sqlite+aiosqlite:///./netsentinel.db \
  --postgres postgresql+asyncpg://netsentinel:netsentinel@localhost:5432/netsentinel
```

### 5. Inicie o servidor
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Acesse o dashboard
Abra **http://localhost:8000** no navegador.

## ⚙️ Variáveis de Ambiente

| Variável | Descrição | Padrão |
|---|---|---|
| `DATABASE_URL` | URL do banco (SQLite ou PostgreSQL) | `sqlite+aiosqlite:///./netsentinel.db` |
| `API_KEY` | Chave exigida nos endpoints de escrita (header `X-API-Key`). **Vazia = escrita aberta.** | _(vazio)_ |
| `CORS_ORIGINS` | Origens permitidas no CORS (separadas por vírgula) | `*` |
| `DB_MONITOR_INTERVAL_SECONDS` | Intervalo entre checagens dos monitores de banco | `30` |
| `DB_LOCK_MIN_WAIT_MS` | Ignora bloqueios com `waitTime` abaixo desse valor (ms); `0` conta todos | `0` |
| `DB_DATA_MAX_AGE_SECONDS` | Marca WARNING se `updatedAt` da Sentinela ficar mais velho que isso; `0` desabilita | `0` |
| `PORTAL_OAB_USERNAME` / `PORTAL_OAB_PASSWORD` | Credenciais do check L7 autenticado do Portal OAB | _(vazio)_ |

## 🗄️ Monitoramento de banco (locks)

Cada monitor de banco consulta um endpoint da API **Sentinela** que retorna as
**sessões bloqueadas/em espera** no banco. O serviço:

- tenta a leitura **até 3×** antes de declarar `DOWN` (absorve blips de rede);
- é **fail-closed**: se o JSON não vier no formato esperado, marca `DOWN` em vez
  de assumir "sem locks" silenciosamente;
- escala `WARNING` (1ª rodada com bloqueios) → `CRITICAL_LOCK` (2ª rodada+);
- opcionalmente ignora bloqueios curtos (`DB_LOCK_MIN_WAIT_MS`) e sinaliza dados
  desatualizados (`DB_DATA_MAX_AGE_SECONDS`).

> ⚠️ Isto detecta **bloqueio sustentado** (contenção), não *deadlock* no sentido
> técnico — deadlocks são resolvidos pelo próprio SGBD em segundos e não aparecem
> como linhas persistentes nesse endpoint.

## 🔒 Autenticação

Os endpoints de **escrita** (`POST`/`PUT`/`DELETE`) são protegidos por token quando
`API_KEY` está definida no `.env`. O cliente envia o header `X-API-Key`. Os endpoints
de **leitura** (`GET`) e o dashboard permanecem abertos.

No frontend, a chave é solicitada automaticamente na primeira ação de escrita que
retornar `401` e guardada no navegador (`localStorage`). Se `API_KEY` não estiver
definida, a escrita fica aberta e o servidor registra um aviso na inicialização.

## 📡 API Endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `GET` | `/api/devices` | — | Lista todos os dispositivos |
| `POST` | `/api/devices` | 🔒 | Cadastra novo dispositivo |
| `PUT` | `/api/devices/{id}` | 🔒 | Atualiza dispositivo |
| `DELETE` | `/api/devices/{id}` | 🔒 | Remove dispositivo |
| `GET` | `/api/devices/{id}/stats` | — | Estatísticas + uptime real (janela de 7 dias) |
| `GET` | `/api/devices/{id}/performance` | — | Histórico de métricas L7 |
| `GET` | `/api/devices/{id}/report/pdf` | — | Relatório em PDF |
| `GET` | `/api/events` | — | Log de eventos recentes |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/db-monitors[...]` | 🔒 (escrita) | Monitores de banco de dados |
| `WS` | `/ws` | — | WebSocket para tempo real |

## 🧪 Testes

```bash
pip install -r requirements.txt
pytest
```

## 🛠️ Tecnologias

- **Backend:** Python, FastAPI, SQLAlchemy (async), asyncpg/aiosqlite, httpx
- **Frontend:** HTML5, CSS3 (vanilla), JavaScript (vanilla)
- **Comunicação:** REST API + WebSocket
- **Banco:** PostgreSQL (async, recomendado) com fallback SQLite

---

> Desenvolvido com 💚 para monitoramento de infraestrutura.

# 🛡️ NetSentinel

**Painel de monitoramento distribuído de dispositivos em tempo real.**

Sistema completo para monitorar dispositivos Web (HTTP/HTTPS) e Hardware (IP/Ping) com alertas sonoros, notificações em tempo real via WebSocket e interface moderna.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Async-003B57?logo=sqlite&logoColor=white)

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
│   ├── database.py      # Engine async SQLite
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

### 1. Clone o repositório
```bash
git clone https://github.com/SEU_USUARIO/Monitotamento.git
cd Monitotamento
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

### 5. Inicie o servidor
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Acesse o dashboard
Abra **http://localhost:8000** no navegador.

## ⚙️ Variáveis de Ambiente

| Variável | Descrição | Padrão |
|---|---|---|
| `DATABASE_URL` | URL do banco SQLite | `sqlite+aiosqlite:///./netsentinel.db` |
| `WORKER_AUTH_KEY` | Chave de autenticação dos workers | `your-super-secret-worker-key` |

## 📡 API Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/devices` | Lista todos os dispositivos |
| `POST` | `/api/devices` | Cadastra novo dispositivo |
| `PUT` | `/api/devices/{id}` | Atualiza dispositivo |
| `DELETE` | `/api/devices/{id}` | Remove dispositivo |
| `GET` | `/api/devices/{id}/stats` | Estatísticas do dispositivo |
| `GET` | `/api/events` | Log de eventos recentes |
| `POST` | `/api/report-interno` | Report de workers (auth) |
| `WS` | `/ws` | WebSocket para tempo real |

## 🛠️ Tecnologias

- **Backend:** Python, FastAPI, SQLAlchemy (async), aiosqlite, httpx
- **Frontend:** HTML5, CSS3 (vanilla), JavaScript (vanilla)
- **Comunicação:** REST API + WebSocket
- **Banco:** SQLite (async)

---

> Desenvolvido com 💚 para monitoramento de infraestrutura.

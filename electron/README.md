# NetSentinel — Casca Electron

Esta pasta é **apenas a casca visual** do NetSentinel. Toda a lógica de
monitoramento continua no FastAPI (`backend/`) e no frontend web (`frontend/`).
O Electron:

- exibe a aplicação web (`http://localhost:8000`) em uma janela nativa;
- mostra uma tela de "Aguardando servidor…" e tenta reconectar a cada 2 s
  enquanto o FastAPI não responde;
- dispara **notificações nativas do SO** quando um serviço cai;
- abre um **overlay fullscreen `alwaysOnTop`** em alertas críticos — mesmo
  com a janela principal minimizada.

Nada aqui origina eventos de monitoramento: a casca apenas **reage** a alertas
que o frontend envia via `window.electronAPI.sendAlert(...)`.

## Arquivos

| Arquivo        | Papel                                                            |
| -------------- | ---------------------------------------------------------------- |
| `main.js`      | Processo principal: janelas, notificações, overlay, IPC.         |
| `preload.js`   | Ponte segura `contextBridge` → expõe `window.electronAPI`.       |
| `overlay.html` | Página fullscreen do alerta crítico.                             |
| `start.js`     | Lançador de dev: sobe o uvicorn, espera o servidor, abre o app.  |
| `assets/icon.png` | Ícone do app (placeholder 512×512).                           |

## Rodando em desenvolvimento

Pré-requisitos: Python com as dependências do projeto instaladas e o
`node`/`npm` disponíveis.

```bash
# 1. (na RAIZ do projeto) crie o venv e instale as dependências Python
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
pip install -r requirements.txt

# 2. (nesta pasta electron/) instale as dependências Node e rode
cd electron
npm install
npm start            # sobe o FastAPI, espera responder e abre o Electron
```

`npm start` executa `node start.js`, que detecta o executável do uvicorn
no venv (`venv\Scripts\uvicorn.exe` no Windows, `venv/bin/uvicorn` no
Linux/Mac), sobe `backend.main:app` na porta 8000, aguarda o servidor
responder e só então abre a casca. Ao fechar o Electron, o FastAPI é
encerrado junto.

> Para abrir só a casca apontando para um FastAPI já em execução, use
> `npm run electron`.

## Build e distribuição

O empacotamento usa `electron-builder`. A configuração `extraResources`
copia **todo o código Python** (exceto `electron/`, `__pycache__`, `.git` e
`node_modules`) para dentro do executável, na pasta `app/`.

> **IMPORTANTE — instale as dependências Python ANTES do build.**
> O `electron-builder` empacota o código-fonte Python, mas **não** instala os
> pacotes do `requirements.txt` nem cria o venv. Garanta que o ambiente Python
> da máquina-alvo tenha as dependências instaladas (ou inclua um venv/python
> embarcado na sua estratégia de distribuição) antes de gerar o instalador.

```bash
cd electron
npm install

# (na raiz) garanta as dependências Python instaladas:
pip install -r ../requirements.txt

# Windows (gera instalador NSIS):
npm run build:win

# Linux (gera AppImage):
npm run build:linux
```

Os artefatos são gerados em `electron/dist/`.

## Detecção de ambiente no frontend

O frontend funciona **igual** no navegador comum e dentro do Electron. Ele
detecta a casca checando `window.electronAPI`:

```js
if (window.electronAPI) {
  window.electronAPI.sendAlert({ name, url, time, critical: true, type: 'web' });
}
```

No navegador comum, `window.electronAPI` é `undefined` e o bloco é ignorado —
nenhuma alteração de comportamento. Esse ponto de integração já existe em
`frontend/app.js` (no handler de mensagens do WebSocket).

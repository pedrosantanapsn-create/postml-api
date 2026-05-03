# PostML API

Backend do sistema PostML — sistema de afiliados Mercado Livre.

## Endpoints

- `GET /health` — Status do servidor
- `GET /scrape-ml?url=...` — Extrair dados de um produto do ML
- `GET /rec-ml?url=...` — Listar produtos de uma pagina de listagem
- `POST /api/dalle` — Gerar imagem com DALL-E 3 (requer API Key OpenAI)

## Rodar localmente

```bash
python server.py
```

Servidor sobe em `http://localhost:8765`

## Deploy no Render

1. Suba este repositorio no GitHub
2. Em Render.com, crie um novo Web Service
3. Conecte ao repositorio
4. Render detecta automaticamente as configuracoes via `render.yaml`
5. Aguarde o build (1-2 minutos)
6. Servico fica disponivel em `https://seu-app.onrender.com`

## Variaveis de Ambiente

Nenhuma variavel obrigatoria. A API Key da OpenAI e enviada por requisicao.

## Stack

- Python 3.11+
- stdlib apenas (sem dependencias externas)

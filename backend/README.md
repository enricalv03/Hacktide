# Backend API

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Run

```bash
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

## Main endpoints

- `GET /health`
- `GET /clients?search=&estado=&prioridad=`
- `GET /clients/{cliente_id}`
- `GET /alerts/daily?limit=25`
- `POST /alerts/{alerta_id}/outcome`

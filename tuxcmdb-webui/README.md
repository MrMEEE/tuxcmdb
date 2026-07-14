# tuxcmdb-webui

A simple Django interface for TuxCMDB.

## Scope

- Login against the existing `apiusers` table
- Asset list/detail pages using the FastAPI endpoints
- Attribute list/create/edit/delete using the FastAPI endpoints
- Datatype list page using the FastAPI endpoint
- Apiuser list/create/edit/delete directly against the shared database
- WebSocket-driven live UI updates for create/update/delete operations

## Install

```bash
cd tuxcmdb-webui
pip install -r requirements.txt
```

## Run

The FastAPI service should already be running.

```bash
cd tuxcmdb-webui
python manage.py runserver
```

From the repository root you can also use the helper, which starts Daphne (ASGI + WebSockets):

```bash
python tuxcmdb-webui.py start
```

To stop the background process:

```bash
python tuxcmdb-webui.py stop
```

Then open:

```text
http://127.0.0.1:8000/
```

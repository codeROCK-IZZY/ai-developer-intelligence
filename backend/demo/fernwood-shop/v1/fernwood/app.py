"""ASGI application: `uvicorn fernwood.app:app`."""

from fastapi import FastAPI

from fernwood.api.routes import router

app = FastAPI(title="Fernwood Outfitters API")
app.include_router(router)

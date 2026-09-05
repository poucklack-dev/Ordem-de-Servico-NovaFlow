from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .api import router
from .config import settings
from sqlalchemy import inspect, text
from .database import Base, SessionLocal, engine
from .seed import seed_demo


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Migrações compatíveis com a primeira versão já instalada.
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "users" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("users")}
            if "failed_login_attempts" not in cols: connection.execute(text("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0"))
            if "locked_until" not in cols: connection.execute(text("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP NULL"))
        if "company_settings" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("company_settings")}
            if "modules" not in cols:
                json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
                connection.execute(text(f"ALTER TABLE company_settings ADD COLUMN modules {json_type}"))
                connection.execute(text("UPDATE company_settings SET modules = '{\"financial\": false, \"agenda\": true, \"plans\": false, \"academy\": false, \"school\": false}' WHERE modules IS NULL"))
        if engine.dialect.name == "postgresql" and "orders" in inspector.get_table_names():
            connection.execute(text("ALTER TABLE orders ALTER COLUMN value DROP NOT NULL"))
    Base.metadata.create_all(engine)
    if settings.demo_seed:
        with SessionLocal() as db: seed_demo(db)
    yield


app = FastAPI(title="NovaFlow API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
Path("uploads").mkdir(exist_ok=True)
app.mount("/uploads",StaticFiles(directory="uploads"),name="uploads")


@app.get("/health")
def health(): return {"status": "ok"}

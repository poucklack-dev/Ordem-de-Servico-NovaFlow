from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from .api import router
from .config import settings
from sqlalchemy import inspect, select, text
from .database import Base, SessionLocal, engine
from .models import CompanySettings
from .seed import seed_demo
from .access_seed import migrate_access_columns, seed_access
from .access_api import router as access_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate_access_columns(engine)
    # Migrações compatíveis com a primeira versão já instalada.
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "users" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("users")}
            if "failed_login_attempts" not in cols: connection.execute(text("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0"))
            if "locked_until" not in cols: connection.execute(text("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP NULL"))
            if "permissions" not in cols:
                json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
                connection.execute(text(f"ALTER TABLE users ADD COLUMN permissions {json_type} NULL"))
        if "company_settings" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("company_settings")}
            if "modules" not in cols:
                json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
                connection.execute(text(f"ALTER TABLE company_settings ADD COLUMN modules {json_type}"))
                connection.execute(text("UPDATE company_settings SET modules = '{\"financial\": false, \"agenda\": true, \"plans\": false, \"academy\": false, \"school\": false}' WHERE modules IS NULL"))
        if "appointments" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("appointments")}
            if "customer_id" not in cols: connection.execute(text("ALTER TABLE appointments ADD COLUMN customer_id INTEGER NULL"))
            if "service_id" not in cols: connection.execute(text("ALTER TABLE appointments ADD COLUMN service_id INTEGER NULL"))
            if "status" not in cols: connection.execute(text("ALTER TABLE appointments ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Agendado'"))
        if "plans" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("plans")}
            if "description" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN description TEXT NULL"))
            if "starts_on" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN starts_on DATE NULL"))
            if "ends_on" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN ends_on DATE NULL"))
            if "auto_renew" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN auto_renew BOOLEAN NOT NULL DEFAULT FALSE"))
            if "max_users" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN max_users INTEGER NULL"))
            if "included_services" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN included_services TEXT NULL"))
            if "status" not in cols: connection.execute(text("ALTER TABLE plans ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Ativo'"))
        if engine.dialect.name == "postgresql" and "orders" in inspector.get_table_names():
            connection.execute(text("ALTER TABLE orders ALTER COLUMN value DROP NOT NULL"))
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if settings.demo_seed: seed_demo(db)
        if not db.scalar(select(CompanySettings)):
            db.add(CompanySettings());db.commit()
        seed_access(db)
    yield


app = FastAPI(title="NovaFlow API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(access_router)
Path("uploads").mkdir(exist_ok=True)


@app.get("/health")
def health(): return {"status": "ok"}

"""Upgrade safety: exact legacy mapping, no guessed identities, repeatable seeds."""

from sqlalchemy import create_engine, func, inspect, select, text

from app.access import authorization
from app.access_seed import migrate_access_columns, seed_access
from app.database import SessionLocal
from app.models import AuditLog, Employee, JobPosition, Permission, Profile, Role, User


def test_schema_upgrade_is_idempotent_and_preserves_legacy_rows(tmp_path):
    legacy_engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        with legacy_engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(120))"))
            connection.execute(text("CREATE TABLE employees (id INTEGER PRIMARY KEY, job_title VARCHAR(100))"))
            connection.execute(text("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR(150))"))
            connection.execute(text("INSERT INTO users (id, name) VALUES (7, 'Conta preservada')"))
            connection.execute(text("INSERT INTO employees (id, job_title) VALUES (9, 'Cargo legado')"))
        migrate_access_columns(legacy_engine)
        migrate_access_columns(legacy_engine)
        inspector = inspect(legacy_engine)
        assert {"employee_id", "profile_id", "scope", "permission_exceptions", "access_migrated"} <= {item["name"] for item in inspector.get_columns("users")}
        assert {"job_position_id", "access_scope", "managed_department_ids"} <= {item["name"] for item in inspector.get_columns("employees")}
        assert {"department_id", "owner_user_id"} <= {item["name"] for item in inspector.get_columns("customers")}
        with legacy_engine.connect() as connection:
            assert connection.execute(text("SELECT id, name FROM users")).all() == [(7, "Conta preservada")]
            assert connection.execute(text("SELECT id, job_title FROM employees")).all() == [(9, "Cargo legado")]
    finally:
        legacy_engine.dispose()


def test_data_upgrade_does_not_guess_links_or_restore_old_grants(client):
    with SessionLocal() as db:
        person = Employee(name="Mesma pessoa", email="legado@example.com", job_title="Diretor Supremo Legado")
        legacy = User(name="Mesma pessoa", email="legado@example.com", password_hash="not-used-by-this-test",
                      role=Role.ATTENDANT, permissions=["financial.view"], access_migrated=False)
        db.add_all([person, legacy])
        db.commit()
        seed_access(db)
        db.refresh(person)
        db.refresh(legacy)
        assert legacy.employee_id is None
        assert legacy.profile.name == "Atendente"
        assert legacy.profile_id is not None
        assert legacy.permissions is None
        assert legacy.permission_exceptions["financial.view"] is True
        assert legacy.permission_exceptions["financial.delete"] is False
        assert person.job_position.name == "Diretor Supremo Legado"
        assert person.job_position.profile.name == "Visualizador"
        assert person.job_position.default_scope == "OWN"
        # A subsequent seed may add missing definitions, but cannot reset edits.
        supervisor = db.scalar(select(Profile).where(Profile.slug == "supervisor"))
        supervisor.permissions = [entry for entry in supervisor.permissions if entry.code != "orders.assign"]
        supervisor.name = "Coordenação revisada"
        default_job = db.scalar(select(JobPosition).where(JobPosition.name == "Analista Administrativo"))
        default_job.name = "Analista Administrativo Revisado"
        person.job_position.default_scope = "DEPARTMENT"
        legacy.permission_exceptions = {}
        db.commit()
        models = (Profile, JobPosition, Permission, AuditLog)
        counts = {model: db.scalar(select(func.count()).select_from(model)) for model in models}
        seed_access(db)
        db.expire_all()
        assert {model: db.scalar(select(func.count()).select_from(model)) for model in models} == counts
        assert "orders.assign" not in {entry.code for entry in supervisor.permissions}
        assert supervisor.name == "Coordenação revisada"
        assert not db.scalar(select(JobPosition.id).where(JobPosition.name == "Analista Administrativo"))
        assert default_job.name == "Analista Administrativo Revisado"
        assert person.job_position.default_scope == "DEPARTMENT"
        assert legacy.permission_exceptions == {}
        assert legacy.profile.name == "Atendente"
        # The obsolete role column is never an alternate authorization source.
        legacy.role = Role.ADMIN
        db.commit()
        actual = authorization(legacy, db)
        assert actual["is_admin"] is False
        assert "roles.manage" not in actual["permissions"]

"""Idempotent access migration; existing identities are never guessed or linked."""
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from .models import AccessMigration, AuditLog, Employee, JobPosition, Permission, Profile, User

PERMISSION_GROUPS = {
    "Sistema": (None, {"system.manage": "Administrar sistema", "settings.manage": "Alterar configurações", "modules.manage": "Ativar e desativar módulos", "users.manage": "Gerenciar usuários", "roles.manage": "Gerenciar cargos e vínculos de acesso", "profiles.manage": "Gerenciar perfis", "permissions.manage": "Gerenciar permissões", "audit.view": "Consultar auditoria completa", "departments.manage": "Gerenciar setores"}),
    "Painel": (None, {"dashboard.view": "Visualizar painel"}),
    "Ordens": (None, {"orders.view": "Consultar ordens", "orders.create": "Criar ordens", "orders.update": "Editar ordens", "orders.delete": "Excluir ordens", "orders.assign": "Atribuir e redistribuir ordens", "orders.change_status": "Alterar status de ordens", "orders.comment": "Comentar ordens", "orders.attachments": "Anexar arquivos às ordens"}),
    "Clientes": (None, {"customers.view": "Consultar clientes", "customers.create": "Cadastrar clientes", "customers.update": "Editar clientes", "customers.delete": "Excluir clientes"}),
    "Funcionários": (None, {"employees.view": "Consultar funcionários no escopo", "employees.view_department": "Consultar funcionários do setor", "employees.create": "Cadastrar funcionários", "employees.update": "Editar funcionários", "employees.delete": "Excluir funcionários"}),
    "Serviços": (None, {"services.view": "Consultar serviços", "services.create": "Cadastrar serviços", "services.update": "Editar serviços", "services.delete": "Excluir serviços"}),
    "Relatórios": (None, {"reports.view": "Consultar relatórios no escopo", "reports.view_department": "Consultar relatórios do setor", "reports.export": "Exportar relatórios e ordens"}),
    "Financeiro": ("financial", {"financial.view": "Consultar financeiro", "financial.create": "Criar cobranças e pagamentos", "financial.update": "Atualizar financeiro", "financial.delete": "Excluir registros financeiros", "financial.export": "Exportar financeiro"}),
    "Agenda": ("agenda", {"appointments.view": "Consultar agenda", "appointments.create": "Agendar compromissos", "appointments.update": "Editar compromissos", "appointments.cancel": "Cancelar compromissos"}),
    "Planos e contratos": ("plans", {"plans.view": "Consultar planos", "plans.create": "Cadastrar e editar planos", "contracts.view": "Consultar contratos", "contracts.manage": "Gerenciar contratos"}),
    "Academia": ("academy", {"gym_students.view": "Consultar alunos da academia", "gym_enrollments.manage": "Gerenciar matrículas da academia", "physical_assessments.manage": "Gerenciar avaliações físicas"}),
    "Escola": ("school", {"students.view": "Consultar alunos da escola", "students.create": "Cadastrar alunos da escola", "classes.view": "Consultar turmas", "classes.manage": "Gerenciar turmas", "enrollments.manage": "Gerenciar matrículas escolares"}),
}

PROFILE_DEFAULTS = {
    "administrador": ("Administrador", "ALL", None),
    "gerente": ("Gerente", "MANAGED_DEPARTMENTS", "dashboard.view orders.view orders.create orders.update orders.delete orders.assign orders.change_status orders.comment orders.attachments customers.view customers.create customers.update employees.view employees.view_department employees.create employees.update services.view services.create services.update reports.view reports.view_department reports.export financial.view financial.create financial.update financial.export appointments.view appointments.create appointments.update appointments.cancel plans.view plans.create contracts.view contracts.manage gym_students.view gym_enrollments.manage physical_assessments.manage students.view students.create classes.view classes.manage enrollments.manage"),
    "supervisor": ("Supervisor", "DEPARTMENT", "dashboard.view orders.view orders.create orders.update orders.assign orders.change_status orders.comment orders.attachments customers.view employees.view employees.view_department services.view reports.view reports.view_department appointments.view appointments.create appointments.update gym_students.view physical_assessments.manage students.view classes.view"),
    "analista": ("Analista", "DEPARTMENT", "dashboard.view orders.view orders.create orders.update orders.comment orders.attachments customers.view customers.create customers.update services.view reports.view reports.view_department reports.export appointments.view plans.view contracts.view gym_students.view students.view classes.view"),
    "atendente": ("Atendente", "DEPARTMENT", "dashboard.view orders.view orders.create orders.update orders.comment orders.attachments customers.view customers.create customers.update employees.view services.view appointments.view appointments.create appointments.update appointments.cancel plans.view contracts.view contracts.manage gym_students.view gym_enrollments.manage students.view students.create classes.view enrollments.manage"),
    "operacional": ("Operacional", "OWN", "dashboard.view orders.view orders.change_status orders.comment orders.attachments customers.view employees.view services.view appointments.view appointments.update gym_students.view physical_assessments.manage students.view classes.view"),
    "financeiro": ("Financeiro", "DEPARTMENT", "dashboard.view orders.view customers.view services.view reports.view reports.export financial.view financial.create financial.update financial.delete financial.export plans.view contracts.view contracts.manage"),
    "visualizador": ("Visualizador", "OWN", "dashboard.view orders.view customers.view services.view reports.view"),
}

JOB_DEFAULTS = {
    "Administrador do Sistema": "administrador", "Gerente Administrativo": "gerente", "Gerente Operacional": "gerente", "Gerente de Operações": "gerente", "Gerente Acadêmico": "gerente", "Gerente da Unidade": "gerente",
    "Supervisor Operacional": "supervisor", "Supervisor de Operações": "supervisor", "Supervisor de Atendimento": "supervisor", "Supervisor de Manutenção": "supervisor", "Supervisor Acadêmico": "supervisor",
    "Analista Administrativo": "analista", "Analista de Operações": "analista", "Analista de Dados": "analista", "Analista Acadêmico": "analista", "Analista de Planejamento": "analista",
    "Atendente": "atendente", "Recepcionista": "atendente", "Técnico": "operacional", "Professor": "operacional", "Operador": "operacional",
    "Assistente Financeiro": "financeiro", "Analista Financeiro": "financeiro", "Analista Financeiro Operacional": "financeiro", "Visualizador": "visualizador", "Auditor / Consulta": "visualizador",
}


def migrate_access_columns(engine):
    inspector = inspect(engine)
    json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    columns = {
        "users": {"employee_id": "INTEGER NULL", "profile_id": "INTEGER NULL", "scope": "VARCHAR(30) NULL", "department_id": "INTEGER NULL", "managed_department_ids": f"{json_type} NULL", "allowed_modules": f"{json_type} NULL", "permission_exceptions": f"{json_type} NULL", "access_migrated": "BOOLEAN NOT NULL DEFAULT FALSE"},
        "employees": {"job_position_id": "INTEGER NULL", "access_scope": "VARCHAR(30) NULL", "managed_department_ids": f"{json_type} NULL"},
    }
    for table in ("customers", "services", "plans", "module_records", "appointments"):
        columns[table] = {"department_id": "INTEGER NULL", "owner_user_id": "INTEGER NULL"}
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, definitions in columns.items():
            if table not in tables:
                continue
            existing = {item["name"] for item in inspector.get_columns(table)}
            for name, definition in definitions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        if "users" in tables:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_employee_id_unique ON users(employee_id)"))


def seed_access(db: Session):
    initialize_defaults = db.get(AccessMigration, "access_defaults_v1") is None
    permission_map = {item.code: item for item in db.scalars(select(Permission)).all()}
    for category, (module, entries) in PERMISSION_GROUPS.items():
        for code, name in entries.items():
            if code not in permission_map:
                item = Permission(code=code, name=name, description=name, module=module, category=category)
                db.add(item)
                permission_map[code] = item
    db.flush()
    profiles = {item.slug: item for item in db.scalars(select(Profile)).all()}
    for slug, (name, scope, codes) in PROFILE_DEFAULTS.items():
        if initialize_defaults and slug not in profiles:
            profile = Profile(name=name, slug=slug, default_scope=scope, is_admin=slug == "administrador", active=True)
            profile.permissions = list(permission_map.values()) if codes is None else [permission_map[code] for code in codes.split()]
            db.add(profile)
            profiles[slug] = profile
    db.flush()
    jobs = {item.name: item for item in db.scalars(select(JobPosition)).all()}
    for name, slug in JOB_DEFAULTS.items():
        if initialize_defaults and name not in jobs:
            job = JobPosition(name=name, profile_id=profiles[slug].id, default_scope=profiles[slug].default_scope, active=True)
            db.add(job)
            jobs[name] = job
    db.flush()
    if initialize_defaults:
        db.add(AccessMigration(name="access_defaults_v1"))

    def fallback_profile():
        if "visualizador" in profiles:
            return profiles["visualizador"]
        if "legado-sem-acesso" not in profiles:
            profile = Profile(name="Legado sem acesso", slug="legado-sem-acesso", description="Conta ou cargo legado sem perfil correspondente; exige revisão administrativa.", default_scope="OWN", active=True, is_admin=False)
            db.add(profile)
            db.flush()
            profiles[profile.slug] = profile
        return profiles["legado-sem-acesso"]
    migrated_employees = 0
    for employee in db.scalars(select(Employee).where(Employee.job_position_id.is_(None))).all():
        # Exact cargo match only. Unknown legacy titles remain least-privileged for admin review.
        if employee.job_title not in jobs:
            job = JobPosition(name=employee.job_title or f"Cargo legado {employee.id}", description="Cargo legado: revise o perfil associado antes de vincular um usuário.", profile_id=fallback_profile().id, default_scope="OWN")
            db.add(job)
            db.flush()
            jobs[employee.job_title] = job
        employee.job_position_id = jobs[employee.job_title].id
        migrated_employees += 1
    migrated_users = 0
    for user in db.scalars(select(User).where(User.access_migrated.is_(False))).all():
        if not user.employee_id and not user.profile_id:
            legacy_slug = next((slug for slug, (name, _, _) in PROFILE_DEFAULTS.items() if name == user.role.value), None)
            profile = profiles.get(legacy_slug) or fallback_profile()
            user.profile_id = profile.id
            user.scope = profile.default_scope
            # No employee linkage or managed departments is inferred from name/e-mail.
            if user.permissions is not None and not profile.is_admin:
                optional_codes = {code for code, permission in permission_map.items() if permission.module}
                user.permission_exceptions = {code: code in user.permissions for code in optional_codes}
            user.permissions = None
            migrated_users += 1
        if user.employee_id:
            user.profile_id = None
            user.scope = None
        user.access_migrated = True
    if migrated_users or migrated_employees:
        db.add(AuditLog(action="ACCESS_MIGRATION", entity="authorization", details={"users": migrated_users, "employees": migrated_employees, "policy": "Perfis legados preservados; nenhum vínculo funcionário/usuário inferido; cargos desconhecidos vinculados ao Visualizador para revisão."}))
    db.commit()

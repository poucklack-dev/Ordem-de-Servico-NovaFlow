from datetime import date, datetime
from enum import Enum
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, Float, ForeignKey, JSON, String, Text, Table, Column, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class Role(str, Enum):
    ADMIN = "Administrador"
    MANAGER = "Gerente"
    SUPERVISOR = "Supervisor"
    ANALYST = "Analista"
    ATTENDANT = "Atendente"
    OPERATOR = "Operacional"
    FINANCE = "Financeiro"
    VIEWER = "Visualizador"


class OrderStatus(str, Enum):
    DRAFT = "Rascunho"
    OPEN = "Aberta"
    ANALYSIS = "Em análise"
    PROGRESS = "Em andamento"
    WAITING = "Aguardando cliente"
    PAUSED = "Pausada"
    DONE = "Concluída"
    CANCELED = "Cancelada"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoMixin:
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class User(Base, TimestampMixin, DemoMixin):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("employee_id IS NULL OR profile_id IS NULL", name="ck_user_single_profile_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    permissions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Legacy role/permissions are retained for data compatibility, never for authorization.
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), unique=True, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    managed_department_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    allowed_modules: Mapped[list | None] = mapped_column(JSON, nullable=True)
    permission_exceptions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    access_migrated: Mapped[bool] = mapped_column(Boolean, default=False)
    employee: Mapped["Employee | None"] = relationship(foreign_keys=[employee_id])
    profile: Mapped["Profile | None"] = relationship(foreign_keys=[profile_id])


profile_permissions = Table(
    "profile_permissions", Base.metadata,
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    module: Mapped[str | None] = mapped_column(String(30), nullable=True)
    category: Mapped[str] = mapped_column(String(80))


class AccessMigration(Base):
    __tablename__ = "access_migrations"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_scope: Mapped[str] = mapped_column(String(30), default="OWN")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[list[Permission]] = relationship(secondary=profile_permissions, lazy="selectin")


class JobPosition(Base, TimestampMixin):
    __tablename__ = "job_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    default_scope: Mapped[str] = mapped_column(String(30), default="OWN")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile: Mapped[Profile] = relationship()
    department: Mapped["Department | None"] = relationship()


class ScopedMixin:
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Customer(Base, TimestampMixin, DemoMixin, ScopedMixin):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    document: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Ativo")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Department(Base, TimestampMixin, DemoMixin):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Employee(Base, TimestampMixin, DemoMixin):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    job_title: Mapped[str] = mapped_column(String(100))
    job_position_id: Mapped[int | None] = mapped_column(ForeignKey("job_positions.id"), nullable=True)
    access_scope: Mapped[str | None] = mapped_column(String(30), nullable=True)
    managed_department_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    job_position: Mapped[JobPosition | None] = relationship()
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Ativo")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    department: Mapped[Department | None] = relationship()


class Service(Base, TimestampMixin, DemoMixin, ScopedMixin):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(100))
    price: Mapped[float] = mapped_column(Float, default=0)
    estimated_minutes: Mapped[int] = mapped_column(default=60)
    sla_hours: Mapped[int] = mapped_column(default=24)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Order(Base, TimestampMixin, DemoMixin):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="Normal")
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.OPEN)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    customer: Mapped[Customer] = relationship()
    service: Mapped[Service] = relationship()
    assignee: Mapped[Employee | None] = relationship()
    history: Mapped[list["OrderHistory"]] = relationship(cascade="all, delete-orphan")


class OrderHistory(Base, DemoMixin):
    __tablename__ = "order_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Payment(Base, TimestampMixin, DemoMixin):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="Pendente")
    method: Mapped[str] = mapped_column(String(30), default="PIX")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompanySettings(Base):
    __tablename__ = "company_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(180), default="Minha empresa")
    segment: Mapped[str] = mapped_column(String(80), default="Serviços")
    primary_color: Mapped[str] = mapped_column(String(10), default="#6D5DFB")
    terms: Mapped[dict] = mapped_column(JSON, default=lambda: {"customer": "Cliente", "order": "Ordem", "employee": "Funcionário", "service": "Serviço"})
    modules: Mapped[dict] = mapped_column(JSON, default=lambda: {"financial": False, "agenda": True, "plans": False, "academy": False, "school": False})


class OrderComment(Base, TimestampMixin, DemoMixin):
    __tablename__ = "order_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=True)
    user: Mapped[User] = relationship()


class ChecklistItem(Base, TimestampMixin, DemoMixin):
    __tablename__ = "order_checklist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(default=0)


class OrderTask(Base, TimestampMixin, DemoMixin):
    __tablename__ = "order_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Pendente")
    priority: Mapped[str] = mapped_column(String(20), default="Normal")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assignee: Mapped[Employee | None] = relationship()


class Appointment(Base, TimestampMixin, DemoMixin, ScopedMixin):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Agendado")
    kind: Mapped[str] = mapped_column(String(30), default="Compromisso")


class Notification(Base, TimestampMixin, DemoMixin):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CustomField(Base, TimestampMixin):
    __tablename__ = "custom_fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[str] = mapped_column(String(30))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomFieldValue(Base, TimestampMixin):
    __tablename__ = "custom_field_values"
    __table_args__ = (UniqueConstraint("field_id", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("custom_fields.id", ondelete="CASCADE"))
    entity_id: Mapped[int] = mapped_column(index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrderAttachment(Base, DemoMixin):
    __tablename__ = "order_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id",ondelete="CASCADE"),index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255),unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)


class Plan(Base,TimestampMixin,DemoMixin,ScopedMixin):
    __tablename__="plans"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(150))
    amount: Mapped[float]=mapped_column(Float)
    periodicity: Mapped[str]=mapped_column(String(30))
    active: Mapped[bool]=mapped_column(Boolean,default=True)
    description: Mapped[str | None]=mapped_column(Text,nullable=True)
    starts_on: Mapped[date | None]=mapped_column(Date,nullable=True)
    ends_on: Mapped[date | None]=mapped_column(Date,nullable=True)
    auto_renew: Mapped[bool]=mapped_column(Boolean,default=False)
    max_users: Mapped[int | None]=mapped_column(nullable=True)
    included_services: Mapped[str | None]=mapped_column(Text,nullable=True)
    status: Mapped[str]=mapped_column(String(30),default="Ativo")


class Subscription(Base,TimestampMixin,DemoMixin):
    __tablename__="subscriptions"
    id: Mapped[int]=mapped_column(primary_key=True)
    plan_id: Mapped[int]=mapped_column(ForeignKey("plans.id"))
    customer_id: Mapped[int]=mapped_column(ForeignKey("customers.id"))
    starts_on: Mapped[date]=mapped_column(Date)
    ends_on: Mapped[date|None]=mapped_column(Date,nullable=True)
    auto_renew: Mapped[bool]=mapped_column(Boolean,default=False)
    status: Mapped[str]=mapped_column(String(30),default="Ativo")


class ModuleRecord(Base, TimestampMixin, DemoMixin, ScopedMixin):
    """Persistent records for optional vertical workflows.

    The resource discriminator keeps the feature framework extensible without
    renaming or duplicating the core customer/order/employee tables.
    """
    __tablename__ = "module_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(30), index=True)
    resource: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="Ativo", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

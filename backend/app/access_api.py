from typing import Literal
import re
import unicodedata
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .access import ADMIN_PERMISSIONS, MODULE_KEYS, SCOPES, authorization, has_permission, require_permission, serialize_user
from .database import get_db
from .models import AuditLog, Department, Employee, JobPosition, Permission, Profile, User
from .schemas import UserIn
from .security import current_user, hash_password

router = APIRouter(prefix="/api")
Scope = Literal["OWN", "DEPARTMENT", "MANAGED_DEPARTMENTS", "ALL"]


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = None
    default_scope: Scope = "OWN"
    active: bool = True
    permissions: list[str] = Field(default_factory=list)
    confirm_affected_users: bool = False
    model_config = ConfigDict(extra="forbid")


class JobPositionIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = None
    profile_id: int = Field(gt=0)
    department_id: int | None = None
    default_scope: Scope = "OWN"
    active: bool = True
    confirm_affected_users: bool = False
    model_config = ConfigDict(extra="forbid")


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)
    is_active: bool | None = None
    employee_id: int | None = None
    profile_id: int | None = None
    scope: Scope | None = None
    department_id: int | None = None
    managed_department_ids: list[int] | None = None
    allowed_modules: list[str] | None = None
    model_config = ConfigDict(extra="forbid")


class PermissionExceptionIn(BaseModel):
    permissions: dict[str, bool]
    reason: str = Field(min_length=8, max_length=1000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        value = value.strip()
        if len(value) < 8:
            raise ValueError("Descreva o motivo da exceção com pelo menos 8 caracteres.")
        return value


def profile_users(db: Session, profile_id: int) -> list[User]:
    employee_ids = select(Employee.id).join(JobPosition, Employee.job_position_id == JobPosition.id).where(JobPosition.profile_id == profile_id)
    return list(db.scalars(select(User).where((User.profile_id == profile_id) | User.employee_id.in_(employee_ids))).all())


def job_users(db: Session, job_id: int) -> list[User]:
    return list(db.scalars(select(User).where(User.employee_id.in_(select(Employee.id).where(Employee.job_position_id == job_id)))).all())


def profile_out(profile: Profile, db: Session) -> dict:
    return {"id": profile.id, "name": profile.name, "slug": profile.slug, "description": profile.description,
            "default_scope": profile.default_scope, "active": profile.active, "is_admin": profile.is_admin,
            "permissions": sorted(item.code for item in profile.permissions), "affected_users": len(profile_users(db, profile.id)),
            "created_at": profile.created_at, "updated_at": profile.updated_at}


def job_out(job: JobPosition, db: Session) -> dict:
    return {"id": job.id, "name": job.name, "description": job.description, "profile_id": job.profile_id,
            "profile": {"id": job.profile.id, "name": job.profile.name, "slug": job.profile.slug},
            "department_id": job.department_id, "department": job.department.name if job.department else None,
            "default_scope": job.default_scope, "active": job.active, "affected_users": len(job_users(db, job.id)),
            "created_at": job.created_at, "updated_at": job.updated_at}


def ensure_administrator_remains(db: Session):
    """Prevent a profile/job/account edit from locking every administrator out."""
    db.flush()
    db.expire_all()
    required = {"system.manage", "users.manage", "roles.manage", "profiles.manage", "permissions.manage"}
    for candidate in db.scalars(select(User).where(User.is_active.is_(True))).all():
        context = authorization(candidate, db)
        if context["is_admin"] and required.issubset(context["permissions"]):
            return
    raise HTTPException(409, "A alteração removeria o último administrador capaz de gerenciar o acesso. Mantenha ao menos um administrador ativo.")


def permissions_from_codes(db: Session, codes: list[str], *, administrator: bool = False) -> list[Permission]:
    requested = set(codes)
    entries = list(db.scalars(select(Permission).where(Permission.code.in_(requested))).all()) if requested else []
    unknown = requested - {item.code for item in entries}
    if unknown:
        raise HTTPException(422, f"Permissões desconhecidas: {', '.join(sorted(unknown))}")
    if not administrator and requested & ADMIN_PERMISSIONS:
        raise HTTPException(422, "Permissões de administração do sistema são exclusivas do perfil Administrador.")
    return entries


def validate_departments(db: Session, department_id: int | None, managed: list[int] | None = None):
    ids = set(managed or []) | ({department_id} if department_id is not None else set())
    existing = set(db.scalars(select(Department.id).where(Department.id.in_(ids))).all()) if ids else set()
    if ids - existing:
        raise HTTPException(422, "Um ou mais setores informados não existem.")


def audit_change(db: Session, actor: User, action: str, entity: str, entity_id: int, old: dict | None, new: dict):
    db.add(AuditLog(user_id=actor.id, action=action, entity=entity, entity_id=str(entity_id), details={"before": old, "after": new}))


def compact_context(user: User, db: Session) -> dict:
    context = authorization(user, db)
    return {**{key: context[key] for key in ("profile_id", "profile_name", "job_position_id", "job_position_name", "scope", "department_id", "department_ids", "employee_id", "permissions")}, "allowed_modules": user.allowed_modules}


@router.get("/auth/context")
def auth_context(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {**authorization(user, db), "user": {"id": user.id, "name": user.name, "email": user.email}}


@router.get("/permissions")
def list_permissions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not any(has_permission(user, code, db) for code in ("permissions.manage", "profiles.manage", "users.manage")):
        raise HTTPException(403, "Acesso exclusivo da administração de permissões.")
    return [{"code": item.code, "name": item.name, "description": item.description, "module": item.module, "category": item.category} for item in db.scalars(select(Permission).order_by(Permission.category, Permission.name)).all()]


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not any(has_permission(user, code, db) for code in ("profiles.manage", "roles.manage", "permissions.manage", "users.manage")):
        raise HTTPException(403, "Acesso exclusivo da administração de perfis.")
    return [profile_out(item, db) for item in db.scalars(select(Profile).order_by(Profile.name)).all()]


@router.post("/profiles", status_code=201)
def create_profile(data: ProfileIn, db: Session = Depends(get_db), user: User = Depends(require_permission("profiles.manage"))):
    if not has_permission(user, "permissions.manage", db):
        raise HTTPException(403, "É necessária permissão para gerenciar permissões.")
    name = data.name.strip()
    if len(name) < 2 or db.scalar(select(Profile).where(func.lower(Profile.name) == name.lower())):
        raise HTTPException(409, "Nome de perfil inválido ou já utilizado.")
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-") or "perfil"
    slug, suffix = base, 2
    while db.scalar(select(Profile).where(Profile.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    profile = Profile(name=name, slug=slug, description=data.description, default_scope=data.default_scope, active=data.active, is_admin=False)
    profile.permissions = permissions_from_codes(db, data.permissions)
    db.add(profile)
    db.flush()
    audit_change(db, user, "PROFILE_CREATED", "profile", profile.id, None, {"name": profile.name, "permissions": data.permissions, "scope": data.default_scope})
    db.commit()
    return profile_out(profile, db)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: int, data: ProfileIn, db: Session = Depends(get_db), user: User = Depends(require_permission("profiles.manage"))):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Perfil não encontrado.")
    previous_permissions = sorted(item.code for item in profile.permissions)
    if previous_permissions != sorted(set(data.permissions)) and not has_permission(user, "permissions.manage", db):
        raise HTTPException(403, "É necessária permissão para gerenciar permissões.")
    if profile.is_admin and data.name.strip() != "Administrador":
        raise HTTPException(422, "O perfil Administrador mantém seu nome reservado para evitar ambiguidade.")
    if db.scalar(select(Profile).where(func.lower(Profile.name) == data.name.strip().lower(), Profile.id != profile_id)):
        raise HTTPException(409, "Nome de perfil já utilizado.")
    permissions = permissions_from_codes(db, data.permissions, administrator=profile.is_admin)
    affected = profile_users(db, profile.id)
    sensitive = previous_permissions != sorted(set(data.permissions)) or profile.default_scope != data.default_scope or profile.active != data.active
    if sensitive and affected and not data.confirm_affected_users:
        raise HTTPException(409, {"message": f"A alteração afetará {len(affected)} usuário(s). Confirme para continuar.", "affected_users": len(affected), "old_profile": profile.name, "new_profile": data.name})
    old = {"name": profile.name, "permissions": previous_permissions, "scope": profile.default_scope, "active": profile.active}
    before_users = {item.id: compact_context(item, db) for item in affected}
    profile.name, profile.description, profile.default_scope, profile.active = data.name.strip(), data.description, data.default_scope, data.active
    profile.permissions = permissions
    ensure_administrator_remains(db)
    audit_change(db, user, "PROFILE_CHANGED", "profile", profile.id, old, {"name": profile.name, "permissions": sorted(data.permissions), "scope": profile.default_scope, "active": profile.active, "affected_users": len(affected)})
    for item in affected:
        audit_change(db, user, "ACCESS_CHANGED", "user", item.id, before_users[item.id], compact_context(item, db))
    db.commit()
    return profile_out(profile, db)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db), user: User = Depends(require_permission("profiles.manage"))):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Perfil não encontrado.")
    if profile.is_admin or profile_users(db, profile_id) or db.scalar(select(JobPosition.id).where(JobPosition.profile_id == profile_id).limit(1)):
        raise HTTPException(409, "O perfil está em uso. Edite seu status com a confirmação dos usuários afetados.")
    audit_change(db, user, "PROFILE_DELETED", "profile", profile.id, {"name": profile.name}, {})
    db.delete(profile)
    db.commit()
    return {"message": "Perfil excluído."}


@router.get("/job-positions")
def list_job_positions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not any(has_permission(user, code, db) for code in ("roles.manage", "employees.view", "employees.view_department", "employees.create", "employees.update", "users.manage")):
        raise HTTPException(403, "Seu perfil não pode consultar cargos.")
    query = select(JobPosition)
    if not has_permission(user, "roles.manage", db):
        query = query.where(JobPosition.active.is_(True))
    rows = [job_out(item, db) for item in db.scalars(query.order_by(JobPosition.name)).all()]
    if not has_permission(user, "roles.manage", db):
        for item in rows:
            item.pop("affected_users", None)
    return rows


@router.post("/job-positions", status_code=201)
def create_job_position(data: JobPositionIn, db: Session = Depends(get_db), user: User = Depends(require_permission("roles.manage"))):
    if db.scalar(select(JobPosition).where(func.lower(JobPosition.name) == data.name.strip().lower())):
        raise HTTPException(409, "Nome de cargo já utilizado.")
    profile = db.get(Profile, data.profile_id)
    if not profile or not profile.active:
        raise HTTPException(422, "Selecione um perfil ativo.")
    validate_departments(db, data.department_id)
    job = JobPosition(**data.model_dump(exclude={"confirm_affected_users"}))
    job.name = job.name.strip()
    db.add(job)
    db.flush()
    audit_change(db, user, "JOB_CREATED", "job_position", job.id, None, {"name": job.name, "profile_id": job.profile_id, "profile": profile.name, "scope": job.default_scope})
    db.commit()
    return job_out(job, db)


@router.put("/job-positions/{job_id}")
def update_job_position(job_id: int, data: JobPositionIn, db: Session = Depends(get_db), user: User = Depends(require_permission("roles.manage"))):
    job = db.get(JobPosition, job_id)
    if not job:
        raise HTTPException(404, "Cargo não encontrado.")
    profile = db.get(Profile, data.profile_id)
    if not profile or not profile.active:
        raise HTTPException(422, "Selecione um perfil ativo.")
    if db.scalar(select(JobPosition).where(func.lower(JobPosition.name) == data.name.strip().lower(), JobPosition.id != job_id)):
        raise HTTPException(409, "Nome de cargo já utilizado.")
    validate_departments(db, data.department_id)
    affected = job_users(db, job_id)
    sensitive = any(getattr(job, key) != getattr(data, key) for key in ("profile_id", "default_scope", "department_id", "active"))
    if sensitive and affected and not data.confirm_affected_users:
        raise HTTPException(409, {"message": f"Alterar este cargo afetará {len(affected)} usuário(s). Confirme para continuar.", "affected_users": len(affected), "old_profile": job.profile.name, "new_profile": profile.name})
    old = {"name": job.name, "profile_id": job.profile_id, "profile": job.profile.name, "scope": job.default_scope, "department_id": job.department_id, "active": job.active}
    before_users = {item.id: compact_context(item, db) for item in affected}
    for key, value in data.model_dump(exclude={"confirm_affected_users"}).items():
        setattr(job, key, value.strip() if key == "name" else value)
    for employee in db.scalars(select(Employee).where(Employee.job_position_id == job_id)).all():
        employee.job_title = job.name
    ensure_administrator_remains(db)
    audit_change(db, user, "JOB_PROFILE_CHANGED" if old["profile_id"] != job.profile_id else "JOB_CHANGED", "job_position", job.id, old, {"name": job.name, "profile_id": job.profile_id, "profile": job.profile.name, "scope": job.default_scope, "department_id": job.department_id, "active": job.active, "affected_users": len(affected)})
    for item in affected:
        audit_change(db, user, "ACCESS_CHANGED", "user", item.id, before_users[item.id], compact_context(item, db))
    db.commit()
    return job_out(job, db)


@router.delete("/job-positions/{job_id}")
def delete_job_position(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_permission("roles.manage"))):
    job = db.get(JobPosition, job_id)
    if not job:
        raise HTTPException(404, "Cargo não encontrado.")
    if db.scalar(select(Employee.id).where(Employee.job_position_id == job_id).limit(1)):
        raise HTTPException(409, "O cargo possui funcionários vinculados. Edite seu status com confirmação.")
    audit_change(db, user, "JOB_DELETED", "job_position", job.id, {"name": job.name}, {})
    db.delete(job)
    db.commit()
    return {"message": "Cargo excluído."}


def validate_user_access(db: Session, values: dict, target_id: int | None = None):
    employee_id, profile_id = values.get("employee_id"), values.get("profile_id")
    if bool(employee_id) == bool(profile_id):
        raise HTTPException(422, "Vincule um funcionário OU informe um perfil direto para uma conta sem funcionário.")
    if employee_id:
        employee = db.get(Employee, employee_id)
        if not employee or employee.deleted_at or employee.status != "Ativo" or not employee.job_position or not employee.job_position.active or not employee.job_position.profile.active:
            raise HTTPException(422, "O funcionário deve estar ativo e possuir cargo e perfil ativos.")
        if values.get("scope") is not None or values.get("department_id") is not None or values.get("managed_department_ids"):
            raise HTTPException(422, "O escopo e os setores de um usuário vinculado são definidos no funcionário e no cargo.")
        other = db.scalar(select(User).where(User.employee_id == employee_id, User.id != target_id)) if target_id else db.scalar(select(User).where(User.employee_id == employee_id))
        if other:
            raise HTTPException(409, "Este funcionário já possui uma conta de acesso.")
    else:
        profile = db.get(Profile, profile_id)
        if not profile or not profile.active:
            raise HTTPException(422, "Selecione um perfil ativo.")
        if values.get("scope") is None:
            values["scope"] = profile.default_scope
        if values["scope"] not in SCOPES:
            raise HTTPException(422, "Escopo inválido.")
        validate_departments(db, values.get("department_id"), values.get("managed_department_ids"))
        if values["scope"] == "DEPARTMENT" and not values.get("department_id"):
            raise HTTPException(422, "Informe o setor para o escopo Próprio setor.")
        if values["scope"] == "MANAGED_DEPARTMENTS" and not values.get("managed_department_ids"):
            raise HTTPException(422, "Selecione ao menos um setor gerenciado.")
    if values.get("allowed_modules") is not None and set(values["allowed_modules"]) - set(MODULE_KEYS):
        raise HTTPException(422, "Módulo autorizado inválido.")


@router.get("/users")
@router.get("/access/users", include_in_schema=False)
def list_users(q: str = "", profile_id: int | None = None, role: str | None = None, is_active: bool | None = None, db: Session = Depends(get_db), user: User = Depends(require_permission("users.manage"))):
    query = select(User)
    if q:
        query = query.where(User.name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    rows = [serialize_user(item, db) for item in db.scalars(query.order_by(User.name)).all()]
    return [item for item in rows if (not profile_id or (item["effective_profile"] and item["effective_profile"]["id"] == profile_id)) and (not role or item["role"] == role)]


@router.post("/users", status_code=201)
@router.post("/access/users", status_code=201, include_in_schema=False)
def create_user(data: UserIn, db: Session = Depends(get_db), user: User = Depends(require_permission("users.manage"))):
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(409, "E-mail já cadastrado.")
    values = data.model_dump(exclude={"password", "email"})
    validate_user_access(db, values)
    target = User(**values, email=data.email.lower(), password_hash=hash_password(data.password), access_migrated=True)
    db.add(target)
    db.flush()
    audit_change(db, user, "USER_CREATED", "user", target.id, None, compact_context(target, db))
    db.commit()
    return serialize_user(target, db)


@router.put("/users/{user_id}")
@router.patch("/users/{user_id}", include_in_schema=False)
@router.put("/access/users/{user_id}", include_in_schema=False)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db), user: User = Depends(require_permission("users.manage"))):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado.")
    incoming = data.model_dump(exclude_unset=True)
    if any(key in incoming and incoming[key] is None for key in ("name", "email", "is_active")):
        raise HTTPException(422, "Nome, e-mail e status não podem ser nulos.")
    if incoming.get("email") and db.scalar(select(User).where(User.email == incoming["email"].lower(), User.id != user_id)):
        raise HTTPException(409, "E-mail já cadastrado.")
    values = {key: getattr(target, key) for key in ("employee_id", "profile_id", "scope", "department_id", "managed_department_ids", "allowed_modules")}
    values.update({key: value for key, value in incoming.items() if key in values})
    if values.get("employee_id") and "employee_id" in incoming:
        # Changing the identity source requires explicit removal of the old direct profile.
        values["scope"] = incoming.get("scope")
        values["department_id"] = incoming.get("department_id")
        values["managed_department_ids"] = incoming.get("managed_department_ids") or []
    validate_user_access(db, values, target.id)
    old = compact_context(target, db)
    old["is_active"] = target.is_active
    for key, value in values.items():
        setattr(target, key, value)
    for key in ("name", "email", "is_active"):
        if key in incoming:
            setattr(target, key, incoming[key].lower() if key == "email" else incoming[key])
    if incoming.get("password"):
        target.password_hash = hash_password(incoming["password"])
    ensure_administrator_remains(db)
    new = compact_context(target, db)
    new["is_active"] = target.is_active
    audit_change(db, user, "USER_ACCESS_CHANGED", "user", target.id, old, new)
    db.commit()
    return serialize_user(target, db)


@router.put("/users/{user_id}/permissions")
@router.patch("/users/{user_id}/permissions", include_in_schema=False)
@router.put("/access/users/{user_id}/exceptions", include_in_schema=False)
def update_user_permissions(user_id: int, data: PermissionExceptionIn, db: Session = Depends(get_db), user: User = Depends(require_permission("permissions.manage"))):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado.")
    context = authorization(target, db)
    permissions_from_codes(db, list(data.permissions), administrator=True)
    if not context["is_admin"] and any(enabled and code in ADMIN_PERMISSIONS for code, enabled in data.permissions.items()):
        raise HTTPException(422, "Exceções individuais não podem conceder administração do sistema a outro perfil.")
    old = target.permission_exceptions or {}
    target.permission_exceptions = dict(data.permissions)
    ensure_administrator_remains(db)
    db.add(AuditLog(user_id=user.id, action="PERMISSION_EXCEPTION_CHANGED", entity="user", entity_id=str(target.id), details={"before": old, "after": target.permission_exceptions, "reason": data.reason.strip()}))
    db.commit()
    return serialize_user(target, db)

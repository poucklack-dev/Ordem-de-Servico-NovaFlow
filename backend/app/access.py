"""Single authorization source: employee -> job -> profile -> grants -> scope."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session
from .database import get_db
from .models import CompanySettings, Profile, User

SCOPES = ("OWN", "DEPARTMENT", "MANAGED_DEPARTMENTS", "ALL")
MODULE_KEYS = ("financial", "agenda", "plans", "academy", "school")
ADMIN_PERMISSIONS = frozenset({"system.manage", "settings.manage", "modules.manage", "users.manage", "roles.manage", "profiles.manage", "permissions.manage", "audit.view", "departments.manage"})


def authorization(user: User, db: Session | None = None, modules: dict | None = None) -> dict:
    db = db or object_session(user)
    employee = user.employee if user.employee_id else None
    job = employee.job_position if employee and not employee.deleted_at and employee.status == "Ativo" else None
    profile = job.profile if job and job.active else (user.profile if not user.employee_id else None)
    if profile and not profile.active:
        profile = None
    if modules is None:
        company = db.scalar(select(CompanySettings)) if db else None
        stored = company.modules if company and isinstance(company.modules, dict) else {}
        modules = {key: bool(stored.get(key, stored.get("gym", False) if key == "academy" else False)) for key in MODULE_KEYS}
    allowed = set(user.allowed_modules) if user.allowed_modules is not None else set(MODULE_KEYS)
    effective_modules = {key: bool(modules.get(key)) and key in allowed for key in MODULE_KEYS}
    permission_objects = {item.code: item for item in profile.permissions} if profile else {}
    grants = set(permission_objects)
    # Exceptions are explicit, auditable overrides of this single effective grant set.
    if profile:
        for code, enabled in (user.permission_exceptions or {}).items():
            if enabled:
                grants.add(code)
            else:
                grants.discard(code)
    if db and grants:
        from .models import Permission
        permission_objects = {item.code: item for item in db.scalars(select(Permission).where(Permission.code.in_(grants))).all()}
    is_admin = bool(profile and profile.is_admin)
    grants = {code for code in grants if code in permission_objects and (not permission_objects[code].module or effective_modules.get(permission_objects[code].module)) and (is_admin or code not in ADMIN_PERMISSIONS)}
    scope = (employee.access_scope or job.default_scope) if job else (user.scope or (profile.default_scope if profile else "OWN"))
    if scope not in SCOPES:
        scope = "OWN"
    department_id = (employee.department_id or (job.department_id if job else None)) if employee else user.department_id
    managed = (employee.managed_department_ids or []) if employee else (user.managed_department_ids or [])
    departments = ([department_id] if department_id else []) if scope in ("OWN", "DEPARTMENT") else (sorted(set(managed)) if scope == "MANAGED_DEPARTMENTS" else [])
    return {
        "profile": {"id": profile.id, "name": profile.name, "slug": profile.slug} if profile else None,
        "profile_id": profile.id if profile else None, "profile_name": profile.name if profile else "Sem acesso",
        "is_admin": is_admin, "permissions": sorted(grants), "scope": scope,
        "department_id": department_id, "department_ids": departments,
        "employee_id": employee.id if employee else None, "job_position_id": job.id if job else None,
        "job_position_name": job.name if job else None, "modules": effective_modules,
        "authorized_modules": sorted(key for key, enabled in effective_modules.items() if enabled and any(p.module == key and p.code in grants for p in permission_objects.values())),
    }


def serialize_user(user: User, db: Session | None = None) -> dict:
    context = authorization(user, db)
    return {"id": user.id, "name": user.name, "email": user.email, "role": context["profile_name"],
            "is_active": user.is_active, "last_login": user.last_login, "employee_id": user.employee_id,
            "profile_id": user.profile_id, "effective_profile": context["profile"], "profile": context["profile"],
            "scope": context["scope"], "department_id": context["department_id"], "department_ids": context["department_ids"],
            "managed_department_ids": user.managed_department_ids or [], "allowed_modules": user.allowed_modules,
            "permission_exceptions": user.permission_exceptions or {}, "permissions": context["permissions"],
            "job_position_id": context["job_position_id"], "job_position_name": context["job_position_name"], "is_admin": context["is_admin"]}


def has_permission(user: User, code: str, db: Session | None = None) -> bool:
    return code in authorization(user, db)["permissions"]


def require_permission(code: str):
    from .security import current_user
    def guard(db: Session = Depends(get_db), user: User = Depends(current_user)) -> User:
        if not has_permission(user, code, db):
            raise HTTPException(403, "Seu perfil não possui permissão para esta ação.")
        return user
    return guard

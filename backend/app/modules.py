from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import get_db
from .models import CompanySettings, Role, User
from .security import current_user

MODULE_KEYS = ("financial", "agenda", "plans", "academy", "school")
PERMISSIONS = {
    "financial": ["financial.view", "financial.create", "financial.update", "financial.delete", "financial.export"],
    "agenda": ["appointments.view", "appointments.create", "appointments.update", "appointments.cancel"],
    "plans": ["plans.view", "plans.create", "contracts.view", "contracts.manage"],
    "academy": ["gym_students.view", "gym_enrollments.manage", "physical_assessments.manage"],
    "school": ["students.view", "students.create", "classes.view", "classes.manage", "enrollments.manage"],
}

def configured_modules(db: Session) -> dict[str, bool]:
    settings = db.scalar(select(CompanySettings))
    stored = settings.modules if settings and isinstance(settings.modules, dict) else {}
    result = {k: bool(stored.get(k, False)) for k in MODULE_KEYS}
    result["academy"] = bool(stored.get("academy", stored.get("gym", False)))
    return result

def validate_modules(values: dict[str, bool]) -> dict[str, bool]:
    unknown = set(values) - set(MODULE_KEYS) - {"gym"}
    if unknown: raise HTTPException(422, f"Módulos desconhecidos: {', '.join(sorted(unknown))}")
    result = {k: bool(values.get(k, False)) for k in MODULE_KEYS}
    result["academy"] = bool(values.get("academy", values.get("gym", False)))
    if result["academy"] and result["school"]: raise HTTPException(422, "Os módulos Academia e Escola representam segmentos diferentes. Desative um deles para continuar.")
    return result

def permissions_for(user: User, modules: dict[str, bool]) -> list[str]:
    from .access import authorization
    return authorization(user, modules=modules)["permissions"]

def require_module(module: str, permission: str | None = None):
    def guard(db: Session = Depends(get_db), user: User = Depends(current_user)) -> User:
        modules = configured_modules(db)
        if not modules.get(module): raise HTTPException(404, "Este módulo não está habilitado.")
        if permission and permission not in permissions_for(user, modules): raise HTTPException(403, "Seu perfil não possui permissão para este recurso.")
        return user
    return guard

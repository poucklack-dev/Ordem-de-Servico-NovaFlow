"""Record boundaries shared by list queries, aggregates and mutations.

Identity and grants come exclusively from access.authorization. Missing sector
or employee assignments never widen a user's access.
"""
from fastapi import HTTPException
from sqlalchemy import and_, false, or_, select, true
from .access import authorization, has_permission
from .models import (Appointment, Customer, Department, Employee, ModuleRecord,
                     Order, OrderTask, Payment, Plan, Service)


def scope_clause(db, user, model):
    ctx = authorization(user, db)
    if ctx["scope"] == "ALL":
        return true()
    own = ctx["scope"] == "OWN"
    employee_id = ctx.get("employee_id")
    departments = ctx.get("department_ids") or []
    if model is Employee:
        return Employee.id == employee_id if own and employee_id else (
            Employee.department_id.in_(departments) if not own else false())
    if model is Department:
        return Department.id.in_(departments)
    if model is Order:
        if own:
            return or_(Order.assignee_id == employee_id,
                       Order.id.in_(select(OrderTask.order_id).where(OrderTask.assignee_id == employee_id))) if employee_id else false()
        return or_(Order.department_id.in_(departments), and_(Order.department_id.is_(None),
            Order.assignee_id.in_(select(Employee.id).where(Employee.department_id.in_(departments)))))
    if model is Payment:
        return Payment.order_id.in_(select(Order.id).where(Order.deleted_at.is_(None), scope_clause(db, user, Order)))
    if model is Appointment:
        if own:
            return or_(Appointment.owner_user_id == user.id,
                Appointment.employee_id == employee_id if employee_id else false())
        return or_(Appointment.department_id.in_(departments), and_(Appointment.department_id.is_(None), or_(
            Appointment.employee_id.in_(select(Employee.id).where(Employee.department_id.in_(departments))),
            Appointment.order_id.in_(select(Order.id).where(scope_clause(db, user, Order))))))
    if model in (Customer, ModuleRecord, Service, Plan):
        boundary = model.owner_user_id == user.id if own else model.department_id.in_(departments)
        if model is Customer:
            return or_(boundary, Customer.id.in_(select(Order.customer_id).where(
                Order.deleted_at.is_(None), scope_clause(db, user, Order))))
        if model in (Service, Plan):
            # Catalog definitions without a sector are shared, but not editable
            # by a scoped account. They contain no customer/employee records.
            return or_(boundary, model.department_id.is_(None))
        return boundary
    return false()


def scoped_select(db, user, model):
    stmt = select(model).where(scope_clause(db, user, model))
    if hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))
    return stmt


def enforce_scope(db, user, obj, *, write=False):
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        raise HTTPException(404, "Registro não encontrado")
    model = type(obj)
    if write and model in (Service, Plan) and obj.department_id is None and authorization(user, db)["scope"] != "ALL":
        raise HTTPException(403, "Somente um acesso global pode alterar este catálogo compartilhado.")
    if db.scalar(select(model.id).where(model.id == obj.id, scope_clause(db, user, model))) is None:
        raise HTTPException(404, "Registro não encontrado no seu escopo de acesso.")
    return obj


def scoped_get(db, user, model, record_id, *, write=False):
    return enforce_scope(db, user, db.get(model, record_id), write=write)


def scope_values(db, user, department_id=None, *, existing=None):
    ctx = authorization(user, db)
    if department_id is None:
        department_id = getattr(existing, "department_id", None)
    if department_id is None and ctx["scope"] != "ALL":
        department_id = next(iter(ctx.get("department_ids") or []), None)
    if department_id is not None and db.get(Department, department_id) is None:
        raise HTTPException(422, "Setor não encontrado.")
    if ctx["scope"] not in ("ALL", "OWN") and (department_id is None or department_id not in ctx["department_ids"]):
        raise HTTPException(403, "Selecione um setor dentro do seu escopo de acesso.")
    if ctx["scope"] == "OWN" and department_id is not None and department_id not in ctx["department_ids"]:
        raise HTTPException(403, "O setor informado não pertence ao seu escopo.")
    return {"department_id": department_id, "owner_user_id": getattr(existing, "owner_user_id", None) or user.id}


def validate_order_values(db, user, values, existing=None):
    ctx = authorization(user, db)
    if existing:
        enforce_scope(db, user, existing, write=True)
    for field, model in (("customer_id", Customer), ("service_id", Service)):
        scoped_get(db, user, model, values[field])
    assignee_id = values.get("assignee_id")
    assignee = scoped_get(db, user, Employee, assignee_id) if assignee_id else None
    if assignee and assignee.status != "Ativo":
        raise HTTPException(422, "O funcionário responsável deve estar ativo.")
    assignment_changed = assignee_id != (existing.assignee_id if existing else None)
    department = values.get("department_id") or (existing.department_id if existing else None)
    if department is None and assignee:
        department = assignee.department_id
    department_changed = existing is not None and department != existing.department_id
    if (assignment_changed or department_changed) and not has_permission(user, "orders.assign", db):
        if not (existing is None and assignee_id == ctx.get("employee_id") and ctx.get("employee_id")):
            raise HTTPException(403, "Seu perfil não permite atribuir ou redistribuir ordens.")
    if existing and values.get("status", existing.status) != existing.status and not has_permission(user, "orders.change_status", db):
        raise HTTPException(403, "Seu perfil não permite alterar o status da ordem.")
    if department is None and ctx["scope"] != "ALL":
        department = next(iter(ctx.get("department_ids") or []), None)
    if department is not None and db.get(Department, department) is None:
        raise HTTPException(422, "Setor não encontrado.")
    if ctx["scope"] == "OWN" and (not ctx.get("employee_id") or assignee_id != ctx["employee_id"]):
        raise HTTPException(403, "Seu escopo permite apenas ordens atribuídas a você.")
    if ctx["scope"] not in ("ALL", "OWN") and department not in ctx["department_ids"]:
        raise HTTPException(403, "A ordem deve pertencer a um setor autorizado.")
    values["department_id"] = department
    return values


def validate_appointment_values(db, user, values, existing=None):
    if existing:
        enforce_scope(db, user, existing, write=True)
    for field, model in (("order_id", Order), ("employee_id", Employee), ("customer_id", Customer), ("service_id", Service)):
        if values.get(field):
            scoped_get(db, user, model, values[field])
    if values.get("department_id") is None and values.get("employee_id"):
        values["department_id"] = db.get(Employee, values["employee_id"]).department_id
    values.update(scope_values(db, user, values.get("department_id"), existing=existing))
    if authorization(user, db)["scope"] == "OWN" and values.get("employee_id") not in (None, authorization(user, db).get("employee_id")):
        raise HTTPException(403, "Seu escopo permite apenas a sua agenda.")
    return values

from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
import csv,io,secrets
from sqlalchemy import func, select, false, or_
from sqlalchemy.orm import Session
from .database import get_db
from .models import AuditLog, Appointment, ChargeHistory, ChecklistItem, CompanySettings, CustomField, Customer, Department, Employee, FinancialPayable, FinancialPayment, FinancialReceipt, ModuleRecord, Notification, Order, OrderAttachment, OrderComment, OrderHistory, OrderStatus, OrderTask, Payment, Plan, RefreshToken, JobPosition, Profile, Role, Service, Subscription, User, utc_now
from .schemas import AppointmentIn, ChangePassword, ChargeContactIn, ChargeIn, ChecklistIn, CommentIn, CustomFieldIn, CustomerIn, CustomerOut, DemoDelete, EmployeeIn, Login, ModuleRecordIn, ModulesIn, OrderIn, OutgoingPaymentIn, PayableIn, PaymentIn, PlanIn, ReceiptIn, ServiceIn, SettingsIn, TaskIn, Token, UserIn
from .security import admin_only, create_refresh_token, create_token, current_user, hash_password, verify_password
from .access import authorization, serialize_user, has_permission, require_permission
from .scopes import scope_clause, scoped_select, scoped_get, enforce_scope, scope_values, validate_order_values, validate_appointment_values
from .modules import PERMISSIONS, configured_modules, permissions_for, require_module, validate_modules
import hashlib

router = APIRouter(prefix="/api")

def require_any_permission(*codes):
    def guard(db: Session = Depends(get_db), user: User = Depends(current_user)):
        if not any(has_permission(user, code, db) for code in codes):
            raise HTTPException(403, "Seu perfil não possui permissão para esta ação.")
        return user
    return guard


def employee_out(x):
    job = x.job_position
    return {"id": x.id, "name": x.name, "job_position_id": x.job_position_id,
            "job_title": job.name if job else "Cargo não definido",
            "profile_id": job.profile_id if job else None,
            "profile_name": job.profile.name if job else "Sem acesso",
            "access_scope": x.access_scope or (job.default_scope if job else "OWN"),
            "access_scope_override": x.access_scope, "managed_department_ids": x.managed_department_ids or [],
            "email": x.email, "phone": x.phone, "status": x.status,
            "department_id": x.department_id, "department": x.department.name if x.department else None}


def employee_access_snapshot(x):
    result = employee_out(x)
    return {key: result[key] for key in ("job_position_id", "job_title", "profile_id", "profile_name",
                                        "access_scope", "department_id", "managed_department_ids", "status")}


def save_employee(data, db, user, employee=None):
    job = db.get(JobPosition, data.job_position_id)
    if not job or not job.active or not job.profile.active:
        raise HTTPException(422, "Selecione um cargo ativo com perfil ativo.")
    values = data.model_dump()
    values["department_id"] = values["department_id"] or job.department_id
    scope = values["access_scope"] or job.default_scope
    if scope == "DEPARTMENT" and not values["department_id"]:
        raise HTTPException(422, "O escopo Próprio setor exige um setor.")
    if scope == "MANAGED_DEPARTMENTS" and not values["managed_department_ids"]:
        raise HTTPException(422, "Selecione os setores gerenciados.")
    dept_ids = set(values["managed_department_ids"])
    if values["department_id"]:
        dept_ids.add(values["department_id"])
    if any(db.get(Department, ident) is None for ident in dept_ids):
        raise HTTPException(422, "Um dos setores informados não existe.")
    before = employee_access_snapshot(employee) if employee else None
    controlled = ("job_position_id", "access_scope", "department_id", "managed_department_ids", "status")
    access_changed = employee is None or any(values[key] != getattr(employee, key) for key in controlled)
    if access_changed and not has_permission(user, "roles.manage", db):
        raise HTTPException(403, "Somente um administrador com permissão para gerenciar cargos pode alterar o vínculo ou escopo.")
    linked_users = db.scalars(select(User).where(User.employee_id == employee.id)).all() if employee else []
    user_contexts = {item.id: authorization(item, db) for item in linked_users}
    if employee is None:
        employee = Employee(job_title=job.name, **values)
        db.add(employee)
    else:
        for key, value in values.items():
            setattr(employee, key, value)
        employee.job_title = job.name
    employee.job_position = job
    db.flush()
    if linked_users:
        from .access_api import ensure_administrator_remains
        ensure_administrator_remains(db)
    after = employee_access_snapshot(employee)
    db.add(AuditLog(user_id=user.id, action="EMPLOYEE_ACCESS_CHANGED" if access_changed else "UPDATE",
                   entity="employee", entity_id=str(employee.id),
                   details={"before": before, "after": after, "affected_user_ids": [item.id for item in linked_users]}))
    if access_changed:
        for linked in linked_users:
            db.add(AuditLog(user_id=user.id, action="USER_ACCESS_CHANGED", entity="user", entity_id=str(linked.id),
                           details={"before": user_contexts[linked.id], "after": authorization(linked, db)}))
    db.commit()
    return employee_out(employee)



MODULE_RESOURCES = {
    "financial": {"charges"},
    "plans": {"contracts", "subscriptions", "renewals"},
    "academy": {"enrollments", "assessments", "modalities"},
    "school": {"guardians", "enrollments", "classes", "courses", "coordination", "documents"},
}
REQUIRED_FIELDS = {
    ("plans", "contracts"): {"name", "customer", "starts_on", "ends_on"},
    ("plans", "subscriptions"): {"customer", "plan"},
    ("financial", "charges"): {"customer", "amount", "due_date"},
    ("academy", "enrollments"): {"number", "student", "starts_on", "modality"},
    ("academy", "assessments"): {"student", "date", "weight", "height"},
    ("academy", "modalities"): {"name"},
    ("school", "guardians"): {"name", "relationship"},
    ("school", "enrollments"): {"number", "student", "course", "class_name", "date"},
    ("school", "classes"): {"code", "name", "course", "capacity"},
    ("school", "courses"): {"name", "workload", "duration"},
    ("school", "coordination"): {"title", "student"},
    ("school", "documents"): {"name", "student"},
}

def check_resource(module:str,resource:str,db:Session,user:User,action:str="view"):
    if module not in MODULE_RESOURCES or resource not in MODULE_RESOURCES[module]: raise HTTPException(404,"Recurso de módulo não encontrado")
    modules=configured_modules(db)
    if not modules[module]: raise HTTPException(404,"Este módulo não está habilitado.")
    permissions=permissions_for(user,modules)
    write=action!="view"
    access={
        ("financial","charges"):f"financial.{action}",
        ("plans","contracts"):"contracts.manage" if write else "contracts.view",
        ("plans","subscriptions"):"contracts.manage" if write else "contracts.view",
        ("plans","renewals"):"contracts.manage" if write else "contracts.view",
        ("academy","enrollments"):"gym_enrollments.manage",
        ("academy","assessments"):"physical_assessments.manage",
        ("academy","modalities"):"gym_enrollments.manage" if write else "gym_students.view",
        ("school","guardians"):"students.create" if write else "students.view",
        ("school","enrollments"):"enrollments.manage",
        ("school","classes"):"classes.manage" if write else "classes.view",
        ("school","courses"):"classes.manage" if write else "classes.view",
        ("school","coordination"):"classes.manage",
        ("school","documents"):"students.create" if write else "students.view",
    }
    required=access[(module,resource)]
    if required not in permissions: raise HTTPException(403,"Seu perfil não possui permissão para este recurso.")

def record_out(x:ModuleRecord): return {"id":x.id,"department_id":x.department_id,"module":x.module,"resource":x.resource,"data":x.data,"status":x.status,"created_at":x.created_at,"updated_at":x.updated_at}

def days_until(value) -> int | None:
    try: return (date.fromisoformat(str(value))-date.today()).days
    except (TypeError,ValueError): return None

def next_billing_date(value: date, periodicity: str | None) -> date:
    months={"Mensal":1,"Bimestral":2,"Trimestral":3,"Semestral":6,"Anual":12}.get(periodicity or "Mensal",1)
    month_index=value.month-1+months;year=value.year+month_index//12;month=month_index%12+1
    month_days=[31,29 if year%4==0 and (year%100!=0 or year%400==0) else 28,31,30,31,30,31,31,30,31,30,31]
    return date(year,month,min(value.day,month_days[month-1]))


def money_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def charge_amount(charge: ModuleRecord) -> Decimal:
    return money_value((charge.data or {}).get("amount"))


def charge_due_date(charge: ModuleRecord) -> date | None:
    try: return date.fromisoformat(str((charge.data or {}).get("due_date")))
    except (TypeError, ValueError): return None


def charge_receipts(db: Session, user: User, charge_id: int):
    return db.scalars(scoped_select(db, user, FinancialReceipt).where(FinancialReceipt.charge_id == charge_id, FinancialReceipt.canceled_at.is_(None))).all()


def computed_charge_status(charge: ModuleRecord, received: Decimal, today: date | None = None) -> str:
    if charge.status == "Cancelado": return "Cancelado"
    amount = charge_amount(charge)
    if received >= amount and amount > 0: return "Pago"
    if received > 0: return "Parcial"
    due = charge_due_date(charge)
    if due and due < (today or date.today()): return "Atrasado"
    return "Pendente"


def charge_out(charge: ModuleRecord, db: Session, user: User, *, include_history: bool = False):
    receipts = charge_receipts(db, user, charge.id)
    received = sum((money_value(x.amount) for x in receipts), Decimal("0.00"))
    amount = charge_amount(charge)
    due = charge_due_date(charge)
    data = dict(charge.data or {})
    status = computed_charge_status(charge, received)
    row = {"id": charge.id, "department_id": charge.department_id, "customer": data.get("customer"), "customer_id": data.get("customer_id"),
           "description": data.get("description") or data.get("plan") or data.get("reference") or "Cobrança", "amount": float(amount),
           "received": float(received), "balance": float(max(Decimal("0.00"), amount - received)), "due_date": due,
           "status": status, "stored_status": charge.status, "origin": data.get("origin") or ("Assinatura" if data.get("subscription_id") else "Manual"),
           "origin_id": data.get("origin_id") or data.get("subscription_id") or data.get("enrollment_id"), "reference": data.get("reference") or data.get("plan"),
           "note": data.get("note"), "created_at": charge.created_at}
    if include_history:
        history = db.scalars(select(ChargeHistory).where(ChargeHistory.charge_id == charge.id).order_by(ChargeHistory.happened_on.desc(), ChargeHistory.created_at.desc())).all()
        row["receipts"] = [receipt_out(x) for x in receipts]
        row["history"] = [{"id": x.id, "event_type": x.event_type, "channel": x.channel, "description": x.description, "happened_on": x.happened_on, "created_at": x.created_at} for x in history]
    return row


def receipt_out(x: FinancialReceipt):
    return {"id": x.id, "charge_id": x.charge_id, "order_id": x.order_id, "customer_id": x.customer_id, "customer": x.customer_name,
            "amount": float(x.amount), "received_on": x.received_on, "method": x.method, "reference": x.reference,
            "origin": x.origin, "note": x.note, "created_at": x.created_at}


def financial_charges(db: Session, user: User):
    rows = db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module == "financial", ModuleRecord.resource == "charges", ModuleRecord.deleted_at.is_(None)).order_by(ModuleRecord.created_at.desc())).all()
    return [charge_out(x, db, user) for x in rows]


def update_stored_charge_status(charge: ModuleRecord, status: str):
    if charge.status != "Cancelado":
        charge.status = status


def create_receipt_record(payload: ReceiptIn, db: Session, user: User) -> FinancialReceipt:
    charge = scoped_get(db, user, ModuleRecord, payload.charge_id, write=True) if payload.charge_id else None
    if charge and (charge.module != "financial" or charge.resource != "charges" or charge.deleted_at):
        raise HTTPException(404, "Cobrança não encontrada.")
    order = scoped_get(db, user, Order, payload.order_id) if payload.order_id else None
    data = dict(charge.data or {}) if charge else {}
    customer = payload.customer or data.get("customer") or (order.customer.name if order else None)
    if not customer: raise HTTPException(422, "Informe o cliente do recebimento.")
    if charge and charge.status == "Cancelado": raise HTTPException(422, "Não é possível receber uma cobrança cancelada.")
    if charge:
        current = sum((money_value(x.amount) for x in charge_receipts(db, user, charge.id)), Decimal("0.00"))
        balance = charge_amount(charge) - current
        if money_value(payload.amount) > balance:
            raise HTTPException(422, "O valor recebido excede o saldo da cobrança.")
    boundary = scope_values(db, user, payload.department_id, existing=charge) if charge else scope_values(db, user, payload.department_id)
    receipt = FinancialReceipt(charge_id=payload.charge_id, order_id=payload.order_id, customer_id=payload.customer_id or data.get("customer_id"),
                               customer_name=customer, amount=payload.amount, received_on=payload.received_on, method=payload.method,
                               reference=payload.reference or data.get("reference"), origin=data.get("origin") or ("Ordem" if order else "Manual"),
                               note=payload.note, created_by_id=user.id, **boundary)
    db.add(receipt);db.flush()
    if charge:
        status = computed_charge_status(charge, sum((money_value(x.amount) for x in charge_receipts(db, user, charge.id)), Decimal("0.00")))
        update_stored_charge_status(charge, status)
        db.add(ChargeHistory(charge_id=charge.id, user_id=user.id, event_type="Recebimento", channel=payload.method, description=f"Recebimento de R$ {money_value(payload.amount)} registrado.", happened_on=payload.received_on, is_demo=charge.is_demo))
    db.add(AuditLog(user_id=user.id, action="RECEIPT", entity="financial_receipt", entity_id=str(receipt.id)))
    return receipt


def payable_payments(db: Session, user: User, payable_id: int):
    return db.scalars(scoped_select(db, user, FinancialPayment).where(FinancialPayment.payable_id == payable_id, FinancialPayment.canceled_at.is_(None))).all()


def computed_payable_status(payable: FinancialPayable, paid: Decimal, today_value: date | None = None) -> str:
    if payable.status == "Cancelado" or payable.canceled_at: return "Cancelado"
    amount = money_value(payable.amount)
    if paid >= amount and amount > 0: return "Pago"
    if paid > 0: return "Parcial"
    if payable.due_date < (today_value or date.today()): return "Atrasado"
    return "Pendente"


def payable_out(payable: FinancialPayable, db: Session, user: User, *, include_payments: bool = False):
    payments = payable_payments(db, user, payable.id)
    paid = sum((money_value(x.amount) for x in payments), Decimal("0.00"))
    amount = money_value(payable.amount)
    row = {"id": payable.id, "department_id": payable.department_id, "beneficiary": payable.beneficiary, "category": payable.category,
           "description": payable.description, "order_id": payable.order_id, "amount": float(amount), "paid": float(paid),
           "balance": float(max(Decimal("0.00"), amount - paid)), "due_date": payable.due_date, "competence": payable.competence,
           "status": computed_payable_status(payable, paid), "stored_status": payable.status, "note": payable.note, "created_at": payable.created_at}
    if include_payments:
        row["payments"] = [outgoing_payment_out(x) for x in payments]
    return row


def outgoing_payment_out(x: FinancialPayment):
    return {"id": x.id, "payable_id": x.payable_id, "order_id": x.order_id, "beneficiary": x.beneficiary,
            "category": x.category, "amount": float(x.amount), "paid_on": x.paid_on, "method": x.method,
            "note": x.note, "created_at": x.created_at}


def financial_payables(db: Session, user: User):
    rows = db.scalars(scoped_select(db, user, FinancialPayable).order_by(FinancialPayable.created_at.desc())).all()
    return [payable_out(x, db, user) for x in rows]


def create_outgoing_payment_record(payload: OutgoingPaymentIn, db: Session, user: User) -> FinancialPayment:
    payable = scoped_get(db, user, FinancialPayable, payload.payable_id, write=True) if payload.payable_id else None
    if payable and computed_payable_status(payable, sum((money_value(x.amount) for x in payable_payments(db, user, payable.id)), Decimal("0.00"))) == "Cancelado":
        raise HTTPException(422, "Não é possível pagar uma conta cancelada.")
    if payable:
        current = sum((money_value(x.amount) for x in payable_payments(db, user, payable.id)), Decimal("0.00"))
        balance = money_value(payable.amount) - current
        if money_value(payload.amount) > balance:
            raise HTTPException(422, "O valor pago excede o saldo da conta.")
    order = scoped_get(db, user, Order, payload.order_id) if payload.order_id else None
    beneficiary = payload.beneficiary or (payable.beneficiary if payable else None)
    if not beneficiary: raise HTTPException(422, "Informe o favorecido do pagamento.")
    boundary = scope_values(db, user, payload.department_id, existing=payable) if payable else scope_values(db, user, payload.department_id)
    payment = FinancialPayment(payable_id=payload.payable_id, order_id=payload.order_id or (payable.order_id if payable else None),
                               beneficiary=beneficiary, category=payload.category or (payable.category if payable else "Operacional"),
                               amount=payload.amount, paid_on=payload.paid_on, method=payload.method, note=payload.note,
                               created_by_id=user.id, **boundary)
    db.add(payment);db.flush()
    if payable:
        payable.status = computed_payable_status(payable, sum((money_value(x.amount) for x in payable_payments(db, user, payable.id)), Decimal("0.00")))
    if order:
        db.add(OrderHistory(order_id=order.id,user_id=user.id,action="CUSTO",new_value=f"Pagamento de R$ {money_value(payload.amount)} para {beneficiary}"))
    db.add(AuditLog(user_id=user.id, action="PAYABLE_PAYMENT", entity="financial_payment", entity_id=str(payment.id)))
    return payment


@router.post("/auth/login", response_model=Token)
def login(data: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    now = utc_now()
    if not user or not user.is_active:
        raise HTTPException(401, "E-mail ou senha inválidos")
    if user.locked_until and user.locked_until > now:
        raise HTTPException(429, "Conta temporariamente bloqueada por excesso de tentativas. Tente novamente em alguns minutos.")
    if not verify_password(data.password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= 5:
            user.locked_until = now + timedelta(minutes=15)
        db.commit()
        raise HTTPException(401, "E-mail ou senha inválidos")
    authorization(user, db)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = utc_now();raw,digest=create_refresh_token();db.add(RefreshToken(user_id=user.id,token_hash=digest,expires_at=utc_now()+timedelta(days=30)));db.commit()
    return Token(access_token=create_token(user),refresh_token=raw,user=serialize_user(user, db))


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return serialize_user(user)


@router.post("/auth/refresh",response_model=Token)
def refresh(refresh_token:str,db:Session=Depends(get_db)):
    digest=hashlib.sha256(refresh_token.encode()).hexdigest();stored=db.scalar(select(RefreshToken).where(RefreshToken.token_hash==digest,RefreshToken.revoked_at.is_(None),RefreshToken.expires_at>utc_now()))
    if not stored:raise HTTPException(401,"Refresh token inválido")
    user=db.get(User,stored.user_id)
    if not user or not user.is_active: raise HTTPException(401,"Sessão inválida ou expirada")
    authorization(user, db)
    stored.revoked_at=utc_now();raw,new_digest=create_refresh_token();db.add(RefreshToken(user_id=user.id,token_hash=new_digest,expires_at=utc_now()+timedelta(days=30)));db.commit();return Token(access_token=create_token(user),refresh_token=raw,user=serialize_user(user, db))


@router.post("/auth/change-password")
def change_password(data:ChangePassword,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not verify_password(data.current_password,user.password_hash):raise HTTPException(400,"Senha atual incorreta")
    user.password_hash=hash_password(data.new_password);db.query(RefreshToken).filter(RefreshToken.user_id==user.id,RefreshToken.revoked_at.is_(None)).update({"revoked_at":utc_now()});db.commit();return {"message":"Senha alterada"}


@router.post("/auth/forgot-password")
def forgot_password(email:str):
    return {"message":"Se o e-mail estiver cadastrado, as instruções serão enviadas."}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_permission("dashboard.view"))):
    modules = configured_modules(db)
    grants=permissions_for(user,modules)
    active = Order.deleted_at.is_(None) & scope_clause(db, user, Order)
    if not has_permission(user, "orders.view", db): active = false()
    total = db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(active)) or 0
    done = db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(active, Order.status == OrderStatus.DONE)) or 0
    open_count = db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(active, Order.status.in_([OrderStatus.OPEN, OrderStatus.ANALYSIS]))) or 0
    progress = db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(active, Order.status == OrderStatus.PROGRESS)) or 0
    overdue = db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(active, Order.due_date < date.today(), Order.status.notin_([OrderStatus.DONE, OrderStatus.CANCELED]))) or 0
    customers = db.scalar(select(func.count(Customer.id)).where(scope_clause(db, user, Customer)).where(Customer.deleted_at.is_(None))) or 0
    status_rows=db.execute(select(Order.status,func.count(Order.id)).where(active).group_by(Order.status)).all();all_orders=db.scalars(scoped_select(db, user, Order).where(active).order_by(Order.created_at.desc())).all();recent_orders=all_orders[:6];order_months={};employee_counts={};completion_hours=[]
    for order in all_orders:
        month=order.created_at.strftime("%m/%Y");order_months[month]=order_months.get(month,0)+1;employee=order.assignee.name if order.assignee else "Não atribuído";employee_counts[employee]=employee_counts.get(employee,0)+1
        if order.completed_at: completion_hours.append(max(0,(order.completed_at-order.created_at).total_seconds()/3600))
    result = {"modules": modules, "core": {"orders": {"total": total, "open": open_count, "progress": progress, "done": done, "overdue": overdue}, "customers": customers, "completion_rate": round(done / total * 100, 1) if total else 0,"sla_rate":round(sum(not x.due_date or x.status==OrderStatus.DONE or x.due_date>=date.today() for x in all_orders)/total*100,1) if total else 0,"average_completion_hours":round(sum(completion_hours)/len(completion_hours),1) if completion_hours else 0,"by_status":[{"label":status.value,"value":count} for status,count in status_rows],"by_month":[{"label":key,"value":value} for key,value in order_months.items()],"by_employee":[{"label":key,"value":value} for key,value in employee_counts.items()],"recent":[{"id":x.id,"number":x.number,"title":x.title,"status":x.status.value} for x in recent_orders]}}
    result["financial"] = None
    if modules["financial"] and "financial.view" in grants:
        received = db.scalar(select(func.sum(Payment.amount)).where(scope_clause(db, user, Payment)).where(Payment.status == "Pago")) or 0
        pending = db.scalar(select(func.sum(Payment.amount)).where(scope_clause(db, user, Payment)).where(Payment.status.in_(["Pendente", "Atrasado"]))) or 0
        charges=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="financial",ModuleRecord.resource=="charges",ModuleRecord.deleted_at.is_(None))).all();received+=sum(float(x.data.get("amount",0) or 0) for x in charges if x.status=="Pago");pending+=sum(float(x.data.get("amount",0) or 0) for x in charges if x.status in ("Pendente","Atrasado"))
        payments=db.scalars(scoped_select(db, user, Payment).order_by(Payment.created_at.desc())).all();methods={};months={}
        for payment in payments: methods[payment.method]=methods.get(payment.method,0)+payment.amount;key=payment.created_at.strftime("%m/%Y");months[key]=months.get(key,0)+payment.amount
        for charge in charges:
            method=str(charge.data.get("method") or "Não informado");amount=float(charge.data.get("amount",0) or 0);methods[method]=methods.get(method,0)+amount
            try: key=date.fromisoformat(str(charge.data.get("due_date"))).strftime("%m/%Y")
            except ValueError: key=charge.created_at.strftime("%m/%Y")
            months[key]=months.get(key,0)+amount
        service_rows=db.execute(select(Service.name,func.sum(Payment.amount)).join(Order,Order.service_id==Service.id).join(Payment,Payment.order_id==Order.id).where(scope_clause(db,user,Order)).group_by(Service.name)).all()
        current_month=sum(x.amount for x in payments if x.created_at.year==date.today().year and x.created_at.month==date.today().month)+sum(float(x.data.get("amount",0) or 0) for x in charges if str(x.data.get("due_date","")).startswith(date.today().strftime("%Y-%m")))
        overdue_count=sum(x.status=="Atrasado" for x in payments+charges);record_count=len(payments)+len(charges);recent=sorted([{"key":f"payment-{x.id}","amount":x.amount,"status":x.status,"method":x.method,"due_date":x.due_date,"created_at":x.created_at} for x in payments]+[{"key":f"charge-{x.id}","amount":float(x.data.get("amount",0) or 0),"status":x.status,"method":x.data.get("method") or "Cobranca","due_date":x.data.get("due_date"),"created_at":x.created_at} for x in charges],key=lambda x:x["created_at"],reverse=True)
        result["financial"] = {"received": received, "pending": pending, "revenue": received + pending,"month_revenue":current_month, "average_ticket": round(received / record_count, 2) if record_count else 0, "overdue": overdue_count,"delinquency_rate":round(overdue_count/record_count*100,1) if record_count else 0,"methods":[{"label":k,"value":v} for k,v in methods.items()],"months":[{"label":k,"value":v} for k,v in months.items()],"received_pending":[{"label":"Recebido","value":received},{"label":"Pendente","value":pending}],"services":[{"label":name,"value":value or 0} for name,value in service_rows],"recent":[{key:value for key,value in x.items() if key!="created_at"} for x in recent[:6]]}
    if modules["agenda"] and "appointments.view" in grants:
        appointments=db.scalars(scoped_select(db, user, Appointment).order_by(Appointment.starts_at)).all();today=[x for x in appointments if x.starts_at.date()==date.today()]
        result["agenda"]={"today":len(today),"confirmed":sum(x.status=="Confirmado" for x in today),"canceled":sum(x.status=="Cancelado" for x in appointments),"next":[{"id":x.id,"title":x.title,"starts_at":x.starts_at,"kind":x.kind,"status":x.status,"customer":scoped_get(db,user,Customer,x.customer_id).name if x.customer_id and scoped_get(db,user,Customer,x.customer_id) else None} for x in today[:8]]}
    else: result["agenda"]=None
    if modules["plans"] and "plans.view" in grants:
        can_view_contracts="contracts.view" in grants;plan_records=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="plans",ModuleRecord.deleted_at.is_(None))).all() if can_view_contracts else [];contracts=[x for x in plan_records if x.resource=="contracts"];subscriptions=[x for x in plan_records if x.resource=="subscriptions"];expiring=[x for x in contracts if days_until(x.data.get("ends_on")) is not None and 0<=days_until(x.data.get("ends_on"))<=30]
        result["plans"]={"active":db.scalar(select(func.count(Plan.id)).where(scope_clause(db, user, Plan)).where(Plan.active.is_(True))) or 0,"active_contracts":sum(x.status=="Ativo" for x in contracts) if can_view_contracts else None,"expiring":len(expiring) if can_view_contracts else None,"renewals":sum(bool(x.data.get("auto_renew")) for x in contracts) if can_view_contracts else None,"new_subscriptions":sum(x.created_at.year==date.today().year and x.created_at.month==date.today().month for x in subscriptions) if can_view_contracts else None,"cancellations":sum(x.status in ("Cancelado","Cancelada") for x in contracts+subscriptions) if can_view_contracts else None,"expiring_contracts":[{"id":x.id,"name":x.data.get("name"),"customer":x.data.get("customer"),"ends_on":x.data.get("ends_on"),"days":days_until(x.data.get("ends_on"))} for x in expiring[:8]]}
    else: result["plans"]=None
    if modules["academy"] and "gym_students.view" in grants:
        records=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="academy",ModuleRecord.deleted_at.is_(None))).all();can_manage_enrollments="gym_enrollments.manage" in grants;enrollments=[x for x in records if x.resource=="enrollments"] if can_manage_enrollments else []
        modality={};plan_counts={}
        for x in enrollments: modality[str(x.data.get("modality") or "Não informado")]=modality.get(str(x.data.get("modality") or "Não informado"),0)+1;plan_counts[str(x.data.get("plan") or "Sem plano")]=plan_counts.get(str(x.data.get("plan") or "Sem plano"),0)+1
        enrollment_months={}
        for x in enrollments: key=x.created_at.strftime("%m/%Y");enrollment_months[key]=enrollment_months.get(key,0)+1
        result["academy"]={"active_students":customers,"active_enrollments":sum(x.status=="Ativa" for x in enrollments) if can_manage_enrollments else None,"new_enrollments":sum(x.created_at.year==date.today().year and x.created_at.month==date.today().month for x in enrollments) if can_manage_enrollments else None,"paused":sum(x.status=="Pausada" for x in enrollments) if can_manage_enrollments else None,"expiring":sum(x.status=="Vencida" or (days_until(x.data.get("ends_on")) is not None and 0<=days_until(x.data.get("ends_on"))<=30) for x in enrollments) if can_manage_enrollments else None,"assessments":sum(x.resource=="assessments" and x.status=="Agendada" for x in records) if "physical_assessments.manage" in grants else None,"favorite_plan":max(plan_counts,key=plan_counts.get) if plan_counts else "Nenhum","by_month":[{"label":k,"value":v} for k,v in enrollment_months.items()],"by_modality":[{"label":k,"value":v} for k,v in modality.items()],"by_plan":[{"label":k,"value":v} for k,v in plan_counts.items()]}
    else: result["academy"]=None
    if modules["school"] and "students.view" in grants:
        records=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="school",ModuleRecord.deleted_at.is_(None))).all();can_view_enrollments="enrollments.manage" in grants;can_view_classes="classes.view" in grants;enrollments=[x for x in records if x.resource=="enrollments"] if can_view_enrollments else [];classes=[x for x in records if x.resource=="classes"] if can_view_classes else []
        by_course={};by_status={}
        for x in enrollments: by_course[str(x.data.get("course") or "Não informado")]=by_course.get(str(x.data.get("course") or "Não informado"),0)+1;by_status[x.status]=by_status.get(x.status,0)+1
        enrollment_months={};by_class={}
        for x in enrollments: key=x.created_at.strftime("%m/%Y");enrollment_months[key]=enrollment_months.get(key,0)+1;class_name=str(x.data.get("class_name") or "Sem turma");by_class[class_name]=by_class.get(class_name,0)+1
        result["school"]={"active_students":customers,"active_enrollments":sum(x.status=="Ativa" for x in enrollments) if can_view_enrollments else None,"new_enrollments":sum(x.created_at.year==date.today().year and x.created_at.month==date.today().month for x in enrollments) if can_view_enrollments else None,"pending_enrollments":sum(x.status=="Pendente" for x in enrollments) if can_view_enrollments else None,"active_classes":sum(x.status=="Ativa" for x in classes) if can_view_classes else None,"active_courses":sum(x.resource=="courses" and x.status=="Ativo" for x in records) if can_view_classes else None,"coordination":sum(x.resource=="coordination" and x.status not in ("Concluído","Cancelado") for x in records) if "classes.manage" in grants else None,"by_month":[{"label":k,"value":v} for k,v in enrollment_months.items()],"by_course":[{"label":k,"value":v} for k,v in by_course.items()],"by_class":[{"label":k,"value":v} for k,v in by_class.items()],"by_status":[{"label":k,"value":v} for k,v in by_status.items()],"classes":[{"id":x.id,"code":x.data.get("code"),"name":x.data.get("name"),"capacity":x.data.get("capacity"),"students":x.data.get("students",0)} for x in classes[:8]]}
    else: result["school"]=None
    return result


@router.get("/customers", response_model=list[CustomerOut])
def customers(q: str = "", status: str | None = None, city: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_any_permission("customers.view","students.view","gym_students.view"))):
    stmt = scoped_select(db, user, Customer).where(Customer.deleted_at.is_(None))
    if q: stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    if status: stmt = stmt.where(Customer.status == status)
    if city: stmt = stmt.where(Customer.city.ilike(f"%{city}%"))
    return db.scalars(stmt.order_by(Customer.created_at.desc())).all()


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(data: CustomerIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    modules=configured_modules(db);grants=permissions_for(user,modules);core_allowed="customers.create" in grants
    if not core_allowed and not (modules["school"] and "students.create" in grants) and not (modules["academy"] and "gym_enrollments.manage" in grants): raise HTTPException(403,"Seu perfil não possui permissão para cadastrar alunos ou clientes.")
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id")))
    obj = Customer(**values); db.add(obj); db.flush()
    db.add(AuditLog(user_id=user.id, action="CREATE", entity="customer", entity_id=str(obj.id)))
    db.commit(); db.refresh(obj); return obj


@router.get("/customers/{customer_id}")
def customer_detail(customer_id:int,db:Session=Depends(get_db),user: User = Depends(require_any_permission("customers.view","students.view","gym_students.view"))):
    x=scoped_get(db,user,Customer,customer_id)
    if not x or x.deleted_at:raise HTTPException(404,"Cliente não encontrado")
    orders=db.scalars(scoped_select(db, user, Order).where(Order.customer_id==x.id,Order.deleted_at.is_(None)).order_by(Order.created_at.desc())).all() if has_permission(user,"orders.view",db) else []
    return {"id":x.id,"department_id":x.department_id,"name":x.name,"document":x.document,"phone":x.phone,"email":x.email,"city":x.city,"state":x.state,"status":x.status,"notes":x.notes,"created_at":x.created_at,"orders":[{"id":o.id,"number":o.number,"title":o.title,"status":o.status.value,"created_at":o.created_at} for o in orders]}


@router.put("/customers/{customer_id}",response_model=CustomerOut)
def update_customer(customer_id:int,data:CustomerIn,db:Session=Depends(get_db),user:User=Depends(require_any_permission("customers.update","students.create","gym_enrollments.manage"))):
    x=scoped_get(db,user,Customer,customer_id)
    if not x or x.deleted_at:raise HTTPException(404,"Cliente não encontrado")
    enforce_scope(db,user,x,write=True)
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id"),existing=x))
    for k,v in values.items():setattr(x,k,v)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="customer",entity_id=str(x.id)));db.commit();db.refresh(x);return x


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id:int,db:Session=Depends(get_db),user:User=Depends(require_permission("customers.delete"))):
    x=scoped_get(db,user,Customer,customer_id)
    if not x:raise HTTPException(404,"Cliente não encontrado")
    x.deleted_at=utc_now();db.add(AuditLog(user_id=user.id,action="DELETE",entity="customer",entity_id=str(x.id)));db.commit();return {"message":"Cliente movido para a lixeira"}


@router.get("/orders")
def orders(status: str | None = None, priority: str | None = None, customer_id: int | None = None, assignee_id: int | None = None, service_id: int | None = None, department_id: int | None = None, overdue: bool = False, q: str = "", db: Session = Depends(get_db), user: User = Depends(require_permission("orders.view"))):
    stmt = scoped_select(db, user, Order).where(Order.deleted_at.is_(None))
    if status: stmt = stmt.where(Order.status == status)
    if priority: stmt = stmt.where(Order.priority == priority)
    if customer_id: stmt = stmt.where(Order.customer_id == customer_id)
    if assignee_id: stmt = stmt.where(Order.assignee_id == assignee_id)
    if service_id: stmt = stmt.where(Order.service_id == service_id)
    if department_id: stmt = stmt.where(Order.department_id == department_id)
    if overdue: stmt = stmt.where(Order.due_date < date.today(), Order.status.notin_([OrderStatus.DONE,OrderStatus.CANCELED]))
    if q: stmt = stmt.where(Order.number.ilike(f"%{q}%") | Order.title.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(Order.created_at.desc())).all()
    modules=configured_modules(db);show_financial=modules["financial"] and "financial.view" in permissions_for(user,modules)
    return [{"id": x.id, "number": x.number, "title": x.title, "customer": x.customer.name, "service": x.service.name, "assignee": x.assignee.name if x.assignee else None, "priority": x.priority, "status": x.status.value, "due_date": x.due_date, "value": x.value if show_financial else None} for x in rows]


@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_permission("orders.view"))):
    x = scoped_get(db,user,Order, order_id)
    if not x or x.deleted_at: raise HTTPException(404, "Ordem não encontrada")
    comments = db.scalars(select(OrderComment).where(OrderComment.order_id == x.id).order_by(OrderComment.created_at.desc())).all()
    checklist = db.scalars(select(ChecklistItem).where(ChecklistItem.order_id == x.id).order_by(ChecklistItem.position)).all()
    tasks = db.scalars(select(OrderTask).where(OrderTask.order_id == x.id)).all()
    history = db.scalars(select(OrderHistory).where(OrderHistory.order_id == x.id).order_by(OrderHistory.created_at.desc())).all()
    modules=configured_modules(db);show_financial=modules["financial"] and "financial.view" in permissions_for(user,modules)
    payments = db.scalars(scoped_select(db, user, Payment).where(Payment.order_id == x.id)).all() if show_financial else []
    attachments=db.scalars(select(OrderAttachment).where(OrderAttachment.order_id==x.id)).all()
    order_charges=[row for row in financial_charges(db,user) if row.get("origin")=="Ordem" and str(row.get("origin_id") or row.get("reference") or "") in (str(x.id),x.number)] if show_financial else []
    order_payables=[row for row in financial_payables(db,user) if row.get("order_id")==x.id] if show_financial else []
    financial_summary={"charges":order_charges,"payables":order_payables,"revenue":sum(row["amount"] for row in order_charges) or (x.value or 0),"received":sum(row["received"] for row in order_charges)+sum(p.amount for p in payments if p.status=="Pago"),"costs":sum(row["amount"] for row in order_payables),"paid":sum(row["paid"] for row in order_payables)} if show_financial else None
    if financial_summary:
        financial_summary["result"]=financial_summary["received"]-financial_summary["paid"]
        financial_summary["margin"]=round(financial_summary["result"]/financial_summary["received"]*100,1) if financial_summary["received"] else 0
    return {"id":x.id,"number":x.number,"title":x.title,"description":x.description,"priority":x.priority,"status":x.status.value,"department_id":x.department_id,"due_date":x.due_date,"value":x.value if show_financial else None,"financial_enabled":show_financial,"financial":financial_summary,"customer":{"id":x.customer.id,"name":x.customer.name,"phone":x.customer.phone},"service":{"id":x.service.id,"name":x.service.name},"assignee":{"id":x.assignee.id,"name":x.assignee.name} if x.assignee else None,"comments":[{"id":c.id,"content":c.content,"internal":c.internal,"author":c.user.name,"created_at":c.created_at} for c in comments],"checklist":[{"id":i.id,"title":i.title,"completed":i.completed} for i in checklist],"tasks":[{"id":t.id,"title":t.title,"status":t.status,"priority":t.priority,"due_date":t.due_date,"assignee":t.assignee.name if t.assignee else None} for t in tasks],"history":[{"id":h.id,"action":h.action,"old_value":h.old_value,"new_value":h.new_value,"created_at":h.created_at} for h in history],"payments":[{"id":p.id,"amount":p.amount,"status":p.status,"method":p.method,"due_date":p.due_date} for p in payments],"attachments":[{"id":a.id,"name":a.original_name,"size":a.size,"url":f"/api/orders/{x.id}/attachments/{a.id}"} for a in attachments]}


@router.post("/orders", status_code=201)
def create_order(data: OrderIn, db: Session = Depends(get_db), user: User = Depends(require_permission("orders.create"))):
    last = db.scalar(select(func.max(Order.id))) or 0
    values=validate_order_values(db,user,data.model_dump());modules=configured_modules(db)
    if "financial.create" not in permissions_for(user,modules): values["value"]=None
    obj = Order(**values, number=f"OS-{datetime.now().year}{last+1:04d}")
    db.add(obj); db.flush(); db.add(OrderHistory(order_id=obj.id, user_id=user.id, action="Ordem criada", new_value=obj.status.value))
    db.add(AuditLog(user_id=user.id, action="CREATE", entity="order", entity_id=str(obj.id))); db.commit()
    return {"id": obj.id, "number": obj.number}


@router.put("/orders/{order_id}")
def update_order(order_id:int,data:OrderIn,db:Session=Depends(get_db),user:User=Depends(require_permission("orders.update"))):
    x=scoped_get(db,user,Order,order_id)
    if not x or x.deleted_at:raise HTTPException(404,"Ordem não encontrada")
    values=validate_order_values(db,user,data.model_dump(),x)
    before={"status":x.status.value,"title":x.title,"assignee_id":x.assignee_id,"department_id":x.department_id}
    modules=configured_modules(db);can_update_financial="financial.update" in permissions_for(user,modules)
    for k,v in values.items():
        if k=="department_id" and v is None: continue
        if k=="value" and not can_update_financial: continue
        setattr(x,k,v)
    db.add(OrderHistory(order_id=x.id,user_id=user.id,action="Ordem atualizada",old_value=str(before),new_value=x.title));db.add(AuditLog(user_id=user.id,action="UPDATE",entity="order",entity_id=str(x.id)));db.commit();return {"message":"Ordem atualizada"}


@router.delete("/orders/{order_id}")
def delete_order(order_id:int,db:Session=Depends(get_db),user:User=Depends(require_permission("orders.delete"))):
    x=scoped_get(db,user,Order,order_id)
    if not x:raise HTTPException(404,"Ordem não encontrada")
    x.deleted_at=utc_now();db.add(AuditLog(user_id=user.id,action="DELETE",entity="order",entity_id=str(x.id)));db.commit();return {"message":"Ordem movida para a lixeira"}


@router.patch("/orders/{order_id}/status")
def update_status(order_id: int, status: OrderStatus, db: Session = Depends(get_db), user: User = Depends(require_permission("orders.change_status"))):
    obj = scoped_get(db,user,Order, order_id)
    if not obj or obj.deleted_at: raise HTTPException(404, "Ordem não encontrada")
    old = obj.status.value; obj.status = status
    if status == OrderStatus.DONE: obj.completed_at = utc_now()
    db.add(OrderHistory(order_id=obj.id, user_id=user.id, action="Status alterado", old_value=old, new_value=status.value))
    db.add(AuditLog(user_id=user.id, action="STATUS_CHANGE", entity="order", entity_id=str(obj.id), details={"from": old, "to": status.value})); db.commit()
    return {"message": "Status atualizado"}


@router.post("/orders/{order_id}/comments", status_code=201)
def add_comment(order_id:int, data:CommentIn, db:Session=Depends(get_db), user:User=Depends(require_permission("orders.comment"))):
    if not scoped_get(db,user,Order,order_id): raise HTTPException(404,"Ordem não encontrada")
    obj=OrderComment(order_id=order_id,user_id=user.id,**data.model_dump());db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Comentário adicionado"));db.commit();return {"id":obj.id}


@router.post("/orders/{order_id}/checklist", status_code=201)
def add_checklist(order_id:int,data:ChecklistIn,db:Session=Depends(get_db),user:User=Depends(require_permission("orders.update"))):
    scoped_get(db,user,Order,order_id,write=True)
    obj=ChecklistItem(order_id=order_id,title=data.title);db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Item de checklist adicionado",new_value=data.title));db.commit();return {"id":obj.id}


@router.patch("/orders/{order_id}/checklist/{item_id}")
def toggle_checklist(order_id:int,item_id:int,completed:bool,db:Session=Depends(get_db),user:User=Depends(require_permission("orders.change_status"))):
    scoped_get(db,user,Order,order_id,write=True)
    obj=db.get(ChecklistItem,item_id)
    if not obj or obj.order_id!=order_id:raise HTTPException(404,"Item não encontrado")
    obj.completed=completed;db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Checklist atualizado",new_value=obj.title));db.commit();return {"completed":completed}


@router.post("/orders/{order_id}/tasks", status_code=201)
def add_task(order_id:int,data:TaskIn,db:Session=Depends(get_db),user:User=Depends(require_permission("orders.assign"))):
    scoped_get(db,user,Order,order_id,write=True)
    if data.assignee_id: scoped_get(db,user,Employee,data.assignee_id)
    obj=OrderTask(order_id=order_id,**data.model_dump());db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Subtarefa criada",new_value=data.title));db.commit();return {"id":obj.id}


@router.get("/employees")
def list_employees(q:str="",status:str|None=None,department_id:int|None=None,job_title:str|None=None,db:Session=Depends(get_db),user: User = Depends(require_any_permission("employees.view","employees.view_department"))):
    stmt = scoped_select(db, user, Employee)
    if q: stmt = stmt.where(Employee.name.ilike(f"%{q}%"))
    if status: stmt = stmt.where(Employee.status == status)
    if department_id: stmt = stmt.where(Employee.department_id == department_id)
    if job_title: stmt = stmt.join(JobPosition).where(JobPosition.name.ilike(f"%{job_title}%"))
    return [employee_out(x) for x in db.scalars(stmt.order_by(Employee.name)).all()]


@router.post("/employees",status_code=201)
def create_employee(data:EmployeeIn,db:Session=Depends(get_db),user:User=Depends(require_permission("employees.create"))):
    return save_employee(data, db, user)


@router.put("/employees/{employee_id}")
def update_employee(employee_id:int,data:EmployeeIn,db:Session=Depends(get_db),user:User=Depends(require_permission("employees.update"))):
    employee = scoped_get(db, user, Employee, employee_id, write=True)
    return save_employee(data, db, user, employee)


@router.get("/services")
def list_services(q:str="",category:str|None=None,active:bool|None=None,db:Session=Depends(get_db),user: User = Depends(require_permission("services.view"))):
    stmt=scoped_select(db, user, Service).where(Service.deleted_at.is_(None))
    if q:stmt=stmt.where(Service.name.ilike(f"%{q}%")|Service.code.ilike(f"%{q}%"))
    if category:stmt=stmt.where(Service.category==category)
    if active is not None:stmt=stmt.where(Service.active==active)
    rows=db.scalars(stmt.order_by(Service.name)).all();show_financial=has_permission(user,"financial.view",db);return [{"id":x.id,"department_id":x.department_id,"code":x.code,"name":x.name,"category":x.category,"price":x.price if show_financial else None,"estimated_minutes":x.estimated_minutes,"sla_hours":x.sla_hours,"active":x.active} for x in rows]


@router.post("/services",status_code=201)
def create_service(data:ServiceIn,db:Session=Depends(get_db),user:User=Depends(require_permission("services.create"))):
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id")))
    if not has_permission(user,"financial.create",db): values["price"]=None
    x=Service(**values);db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="service",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.put("/services/{service_id}")
def update_service(service_id:int,data:ServiceIn,db:Session=Depends(get_db),user:User=Depends(require_permission("services.update"))):
    x=scoped_get(db,user,Service,service_id)
    if not x or x.deleted_at:raise HTTPException(404,"Serviço não encontrado")
    enforce_scope(db,user,x,write=True)
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id"),existing=x))
    for k,v in values.items():
        if k=="price" and not has_permission(user,"financial.update",db): continue
        setattr(x,k,v)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="service",entity_id=str(x.id)));db.commit();return {"message":"Serviço atualizado"}


@router.get("/payments")
def list_payments(status:str|None=None,method:str|None=None,order_id:int|None=None,db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    stmt=scoped_select(db, user, Payment)
    if status:stmt=stmt.where(Payment.status==status)
    if method:stmt=stmt.where(Payment.method==method)
    if order_id:stmt=stmt.where(Payment.order_id==order_id)
    rows=db.scalars(stmt.order_by(Payment.created_at.desc())).all();return [{"id":x.id,"order_id":x.order_id,"order":scoped_get(db,user,Order,x.order_id).number,"amount":x.amount,"status":x.status,"method":x.method,"due_date":x.due_date} for x in rows]


@router.post("/payments",status_code=201)
def create_payment(data:PaymentIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    if not scoped_get(db,user,Order,data.order_id):raise HTTPException(404,"Ordem não encontrada")
    x=Payment(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="PAYMENT",entity="payment",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.patch("/payments/{payment_id}")
def update_payment(payment_id:int,status:str,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.update"))):
    x=scoped_get(db,user,Payment,payment_id)
    if not x:raise HTTPException(404,"Pagamento não encontrado")
    x.status=status;db.add(AuditLog(user_id=user.id,action="PAYMENT",entity="payment",entity_id=str(x.id),details={"status":status}));db.commit();return {"message":"Pagamento atualizado"}


@router.get("/financial/charges")
def list_charges(status:str|None=None,customer:str|None=None,origin:str|None=None,due_from:date|None=None,due_to:date|None=None,overdue:bool=False,upcoming:bool=False,q:str="",db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    rows = financial_charges(db, user)
    if status: rows = [x for x in rows if x["status"] == status]
    if customer: rows = [x for x in rows if customer.casefold() in str(x.get("customer") or "").casefold()]
    if origin: rows = [x for x in rows if x["origin"] == origin]
    if due_from: rows = [x for x in rows if x["due_date"] and x["due_date"] >= due_from]
    if due_to: rows = [x for x in rows if x["due_date"] and x["due_date"] <= due_to]
    if overdue: rows = [x for x in rows if x["status"] == "Atrasado" and x["balance"] > 0]
    if upcoming: rows = [x for x in rows if x["due_date"] and x["due_date"] >= date.today() and x["balance"] > 0]
    if q: rows = [x for x in rows if q.casefold() in " ".join(str(x.get(k) or "") for k in ("customer","description","reference","origin","origin_id")).casefold()]
    return rows


@router.post("/financial/charges",status_code=201)
def create_charge(data:ChargeIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    payload=data.model_dump()
    amount=payload.pop("amount")
    department_id=payload.pop("department_id")
    x=ModuleRecord(module="financial",resource="charges",data={**payload,"amount":float(amount),"due_date":payload["due_date"].isoformat()},status="Pendente",**scope_values(db,user,department_id))
    db.add(x);db.flush()
    db.add(ChargeHistory(charge_id=x.id,user_id=user.id,event_type="Criação",description="Cobrança criada.",happened_on=date.today()))
    db.add(AuditLog(user_id=user.id,action="CREATE",entity="financial.charge",entity_id=str(x.id)));db.commit();db.refresh(x)
    return charge_out(x,db,user,include_history=True)


@router.get("/financial/charges/{charge_id}")
def get_charge(charge_id:int,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.view"))):
    x=scoped_get(db,user,ModuleRecord,charge_id)
    if x.module!="financial" or x.resource!="charges": raise HTTPException(404,"Cobrança não encontrada")
    return charge_out(x,db,user,include_history=True)


@router.put("/financial/charges/{charge_id}")
def update_charge(charge_id:int,data:ChargeIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.update"))):
    x=scoped_get(db,user,ModuleRecord,charge_id,write=True)
    if x.module!="financial" or x.resource!="charges" or x.status=="Cancelado": raise HTTPException(404,"Cobrança não encontrada")
    if charge_receipts(db,user,x.id): raise HTTPException(422,"Cobranças com recebimento registrado não podem ter valor alterado.")
    payload=data.model_dump();amount=payload.pop("amount");department_id=payload.pop("department_id")
    boundary=scope_values(db,user,department_id,existing=x);x.department_id=boundary["department_id"];x.owner_user_id=boundary["owner_user_id"]
    x.data={**payload,"amount":float(amount),"due_date":payload["due_date"].isoformat()}
    update_stored_charge_status(x, computed_charge_status(x, Decimal("0.00")))
    db.add(ChargeHistory(charge_id=x.id,user_id=user.id,event_type="Edição",description="Cobrança editada.",happened_on=date.today()))
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="financial.charge",entity_id=str(x.id)));db.commit()
    return charge_out(x,db,user,include_history=True)


@router.post("/financial/charges/{charge_id}/cancel")
def cancel_charge(charge_id:int,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.delete"))):
    x=scoped_get(db,user,ModuleRecord,charge_id,write=True)
    if x.module!="financial" or x.resource!="charges": raise HTTPException(404,"Cobrança não encontrada")
    x.status="Cancelado";data=dict(x.data or {});data["canceled_at"]=utc_now().isoformat();x.data=data
    db.add(ChargeHistory(charge_id=x.id,user_id=user.id,event_type="Cancelamento",description="Cobrança cancelada.",happened_on=date.today()))
    db.add(AuditLog(user_id=user.id,action="CANCEL",entity="financial.charge",entity_id=str(x.id)));db.commit()
    return {"message":"Cobrança cancelada"}


@router.post("/financial/charges/{charge_id}/receipts",status_code=201)
def receive_charge(charge_id:int,data:ReceiptIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    receipt = create_receipt_record(data.model_copy(update={"charge_id": charge_id}), db, user)
    db.commit()
    return receipt_out(receipt)


@router.post("/financial/receipts",status_code=201)
def create_receipt(data:ReceiptIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    receipt=create_receipt_record(data,db,user);db.commit();return receipt_out(receipt)


@router.get("/financial/receipts")
def list_receipts(method:str|None=None,origin:str|None=None,customer:str|None=None,received_from:date|None=None,received_to:date|None=None,q:str="",db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    rows=[receipt_out(x) for x in db.scalars(scoped_select(db,user,FinancialReceipt).where(FinancialReceipt.canceled_at.is_(None)).order_by(FinancialReceipt.received_on.desc(),FinancialReceipt.created_at.desc())).all()]
    legacy=db.scalars(scoped_select(db,user,Payment).where(Payment.status=="Pago").order_by(Payment.created_at.desc())).all()
    for x in legacy:
        order=scoped_get(db,user,Order,x.order_id)
        rows.append({"id":f"legacy-{x.id}","charge_id":None,"order_id":x.order_id,"customer_id":order.customer_id,"customer":order.customer.name,"amount":x.amount,"received_on":x.created_at.date(),"method":x.method,"reference":order.number,"origin":"Ordem","note":"Recebimento legado de ordem","created_at":x.created_at})
    if method: rows=[x for x in rows if x["method"]==method]
    if origin: rows=[x for x in rows if x["origin"]==origin]
    if customer: rows=[x for x in rows if customer.casefold() in str(x["customer"]).casefold()]
    if received_from: rows=[x for x in rows if date.fromisoformat(str(x["received_on"]))>=received_from]
    if received_to: rows=[x for x in rows if date.fromisoformat(str(x["received_on"]))<=received_to]
    if q: rows=[x for x in rows if q.casefold() in " ".join(str(x.get(k) or "") for k in ("customer","reference","origin","method")).casefold()]
    return rows


@router.get("/financial/payables")
def list_payables(status:str|None=None,category:str|None=None,beneficiary:str|None=None,order_id:int|None=None,q:str="",db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    rows=financial_payables(db,user)
    if status: rows=[x for x in rows if x["status"]==status]
    if category: rows=[x for x in rows if x["category"]==category]
    if beneficiary: rows=[x for x in rows if beneficiary.casefold() in x["beneficiary"].casefold()]
    if order_id: rows=[x for x in rows if x["order_id"]==order_id]
    if q: rows=[x for x in rows if q.casefold() in " ".join(str(x.get(k) or "") for k in ("beneficiary","category","description","order_id")).casefold()]
    return rows


@router.post("/financial/payables",status_code=201)
def create_payable(data:PayableIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    values=data.model_dump();department_id=values.pop("department_id")
    if values.get("order_id"): scoped_get(db,user,Order,values["order_id"])
    x=FinancialPayable(**values,status="Pendente",created_by_id=user.id,**scope_values(db,user,department_id))
    db.add(x);db.flush()
    if x.order_id: db.add(OrderHistory(order_id=x.order_id,user_id=user.id,action="CUSTO",new_value=f"Conta a pagar de R$ {money_value(x.amount)} criada para {x.beneficiary}"))
    db.add(AuditLog(user_id=user.id,action="CREATE",entity="financial.payable",entity_id=str(x.id),details={"amount":float(x.amount)}));db.commit();db.refresh(x)
    return payable_out(x,db,user,include_payments=True)


@router.get("/financial/payables/{payable_id}")
def get_payable(payable_id:int,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.view"))):
    return payable_out(scoped_get(db,user,FinancialPayable,payable_id),db,user,include_payments=True)


@router.put("/financial/payables/{payable_id}")
def update_payable(payable_id:int,data:PayableIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.update"))):
    x=scoped_get(db,user,FinancialPayable,payable_id,write=True)
    if payable_payments(db,user,x.id): raise HTTPException(422,"Contas com pagamento registrado não podem ter valor alterado.")
    values=data.model_dump();department_id=values.pop("department_id")
    if values.get("order_id"): scoped_get(db,user,Order,values["order_id"])
    boundary=scope_values(db,user,department_id,existing=x)
    for key,value in values.items(): setattr(x,key,value)
    x.department_id=boundary["department_id"];x.owner_user_id=boundary["owner_user_id"];x.status=computed_payable_status(x,Decimal("0.00"))
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="financial.payable",entity_id=str(x.id),details={"amount":float(x.amount)}));db.commit()
    return payable_out(x,db,user,include_payments=True)


@router.post("/financial/payables/{payable_id}/cancel")
def cancel_payable(payable_id:int,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.delete"))):
    x=scoped_get(db,user,FinancialPayable,payable_id,write=True)
    x.status="Cancelado";x.canceled_at=utc_now()
    db.add(AuditLog(user_id=user.id,action="CANCEL",entity="financial.payable",entity_id=str(x.id)));db.commit()
    return {"message":"Conta a pagar cancelada"}


@router.post("/financial/payables/{payable_id}/payments",status_code=201)
def pay_payable(payable_id:int,data:OutgoingPaymentIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    payment=create_outgoing_payment_record(data.model_copy(update={"payable_id":payable_id}),db,user);db.commit();return outgoing_payment_out(payment)


@router.post("/financial/payments",status_code=201)
def create_outgoing_payment(data:OutgoingPaymentIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.create"))):
    payment=create_outgoing_payment_record(data,db,user);db.commit();return outgoing_payment_out(payment)


@router.get("/financial/payments")
def list_outgoing_payments(method:str|None=None,category:str|None=None,beneficiary:str|None=None,order_id:int|None=None,q:str="",db:Session=Depends(get_db),user:User=Depends(require_module("financial","financial.view"))):
    rows=[outgoing_payment_out(x) for x in db.scalars(scoped_select(db,user,FinancialPayment).where(FinancialPayment.canceled_at.is_(None)).order_by(FinancialPayment.paid_on.desc(),FinancialPayment.created_at.desc())).all()]
    if method: rows=[x for x in rows if x["method"]==method]
    if category: rows=[x for x in rows if x["category"]==category]
    if beneficiary: rows=[x for x in rows if beneficiary.casefold() in x["beneficiary"].casefold()]
    if order_id: rows=[x for x in rows if x["order_id"]==order_id]
    if q: rows=[x for x in rows if q.casefold() in " ".join(str(x.get(k) or "") for k in ("beneficiary","category","order_id","method")).casefold()]
    return rows


@router.post("/financial/charges/{charge_id}/contacts",status_code=201)
def register_charge_contact(charge_id:int,data:ChargeContactIn,db:Session=Depends(get_db),user:User=Depends(require_module("financial", "financial.update"))):
    x=scoped_get(db,user,ModuleRecord,charge_id)
    if x.module!="financial" or x.resource!="charges": raise HTTPException(404,"Cobrança não encontrada")
    db.add(ChargeHistory(charge_id=x.id,user_id=user.id,event_type="Contato",channel=data.channel,description=data.description,happened_on=data.happened_on,is_demo=x.is_demo))
    db.add(AuditLog(user_id=user.id,action="CONTACT",entity="financial.charge",entity_id=str(x.id)));db.commit();return {"message":"Contato registrado"}


@router.get("/financial/overview")
def financial_overview(db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    charges=financial_charges(db,user);receipts=list_receipts(db=db,user=user);payables=financial_payables(db,user);payments=list_outgoing_payments(db=db,user=user)
    today=date.today();month_start=today.replace(day=1);last30=today-timedelta(days=30)
    received_month=sum(x["amount"] for x in receipts if date.fromisoformat(str(x["received_on"]))>=month_start)
    paid_month=sum(x["amount"] for x in payments if date.fromisoformat(str(x["paid_on"]))>=month_start)
    received_today=sum(x["amount"] for x in receipts if date.fromisoformat(str(x["received_on"]))==today)
    paid_today=sum(x["amount"] for x in payments if date.fromisoformat(str(x["paid_on"]))==today)
    received_30=sum(x["amount"] for x in receipts if date.fromisoformat(str(x["received_on"]))>=last30)
    open_rows=[x for x in charges if x["status"] not in ("Pago","Cancelado") and x["balance"]>0]
    open_payables=[x for x in payables if x["status"] not in ("Pago","Cancelado") and x["balance"]>0]
    overdue=[x for x in open_rows if x["status"]=="Atrasado"]
    overdue_payables=[x for x in open_payables if x["status"]=="Atrasado"]
    by_status={};by_origin={};by_month={}
    for row in charges:
        by_status[row["status"]]=by_status.get(row["status"],0)+1
        by_origin[row["origin"]]=by_origin.get(row["origin"],0)+row["amount"]
    for row in receipts:
        key=date.fromisoformat(str(row["received_on"])).strftime("%m/%Y");by_month[key]=by_month.get(key,0)+row["amount"]
    upcoming=sorted([x for x in open_rows if x["due_date"] and x["due_date"]>=today],key=lambda x:x["due_date"])[:6]
    next_payables=sorted([x for x in open_payables if x["due_date"] and x["due_date"]>=today],key=lambda x:x["due_date"])[:6]
    projected=received_month-paid_month+sum(x["balance"] for x in open_rows)-sum(x["balance"] for x in open_payables)
    alerts=[]
    due_today=sum(1 for x in open_rows if x["due_date"]==today);payables_today=sum(1 for x in open_payables if x["due_date"]==today)
    if due_today: alerts.append(f"{due_today} conta(s) a receber vencem hoje")
    if payables_today: alerts.append(f"{payables_today} conta(s) a pagar vencem hoje")
    if overdue_payables: alerts.append(f"{len(overdue_payables)} conta(s) a pagar atrasadas")
    if overdue: alerts.append(f"{len({x['customer'] for x in overdue})} cliente(s) inadimplentes")
    return {"cards":{"received_month":received_month,"paid_month":paid_month,"month_result":received_month-paid_month,"open":sum(x["balance"] for x in open_rows),"payable_open":sum(x["balance"] for x in open_payables),"overdue":sum(x["balance"] for x in overdue),"payable_overdue":sum(x["balance"] for x in overdue_payables),"delinquent_customers":len({x["customer"] for x in overdue}),"received_today":received_today,"paid_today":paid_today,"received_30":received_30,"upcoming":len(upcoming),"projected_result":projected,"delinquency_rate":round(len(overdue)/len(charges)*100,1) if charges else 0},"months":[{"label":k,"value":v} for k,v in by_month.items()],"status":[{"label":k,"value":v} for k,v in by_status.items()],"origins":[{"label":k,"value":v} for k,v in by_origin.items()],"upcoming":upcoming,"next_payables":next_payables,"latest_receipts":receipts[:6],"latest_payments":payments[:6],"alerts":alerts}


@router.get("/financial/delinquency")
def financial_delinquency(bucket:str|None=None,db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    today=date.today();rows=[]
    for row in financial_charges(db,user):
        if row["status"]=="Cancelado" or row["balance"]<=0 or not row["due_date"] or row["due_date"]>=today: continue
        days=(today-row["due_date"]).days
        band="1-7 dias" if days<=7 else "8-15 dias" if days<=15 else "16-30 dias" if days<=30 else "31-60 dias" if days<=60 else "60+ dias"
        detail=get_charge(row["id"],db,user);contacts=[x for x in detail.get("history",[]) if x["event_type"]=="Contato"]
        row={**row,"days_overdue":days,"bucket":band,"last_contact":contacts[0]["happened_on"] if contacts else None}
        rows.append(row)
    if bucket: rows=[x for x in rows if x["bucket"]==bucket]
    rows=sorted(rows,key=lambda x:(x["days_overdue"],x["balance"]),reverse=True)
    return {"cards":{"overdue_value":sum(x["balance"] for x in rows),"customers":len({x["customer"] for x in rows}),"charges":len(rows),"average_days":round(sum(x["days_overdue"] for x in rows)/len(rows),1) if rows else 0},"buckets":[{"label":label,"value":sum(1 for x in rows if x["bucket"]==label)} for label in ["1-7 dias","8-15 dias","16-30 dias","31-60 dias","60+ dias"]],"rows":rows}


@router.get("/financial/reports")
def financial_reports(period_from:date|None=None,period_to:date|None=None,db:Session=Depends(get_db),user: User = Depends(require_module("financial", "financial.view"))):
    charges=financial_charges(db,user);receipts=list_receipts(db=db,user=user);payables=financial_payables(db,user);payments=list_outgoing_payments(db=db,user=user)
    if period_from: receipts=[x for x in receipts if date.fromisoformat(str(x["received_on"]))>=period_from]
    if period_to: receipts=[x for x in receipts if date.fromisoformat(str(x["received_on"]))<=period_to]
    if period_from: payments=[x for x in payments if date.fromisoformat(str(x["paid_on"]))>=period_from]
    if period_to: payments=[x for x in payments if date.fromisoformat(str(x["paid_on"]))<=period_to]
    month={};expense_month={};result_month={};origin={};method={};expense_category={};payment_method={}
    for r in receipts:
        month_key=date.fromisoformat(str(r["received_on"])).strftime("%m/%Y");month[month_key]=month.get(month_key,0)+r["amount"];origin[r["origin"]]=origin.get(r["origin"],0)+r["amount"];method[r["method"]]=method.get(r["method"],0)+r["amount"]
    for p in payments:
        key=date.fromisoformat(str(p["paid_on"])).strftime("%m/%Y");expense_month[key]=expense_month.get(key,0)+p["amount"];expense_category[p["category"]]=expense_category.get(p["category"],0)+p["amount"];payment_method[p["method"]]=payment_method.get(p["method"],0)+p["amount"]
    for key in set(month)|set(expense_month): result_month[key]=month.get(key,0)-expense_month.get(key,0)
    open_value=sum(x["balance"] for x in charges if x["status"] not in ("Pago","Cancelado"));overdue_value=sum(x["balance"] for x in charges if x["status"]=="Atrasado")
    payable_open=sum(x["balance"] for x in payables if x["status"] not in ("Pago","Cancelado"));paid=sum(x["amount"] for x in payments);received=sum(x["amount"] for x in receipts)
    return {"summary":{"received":received,"paid":paid,"result":received-paid,"margin":round((received-paid)/received*100,1) if received else 0,"open":open_value,"payable_open":payable_open,"overdue":overdue_value,"delinquency_rate":round(overdue_value/open_value*100,1) if open_value else 0,"average_ticket":round(received/len(receipts),2) if receipts else 0,"average_payment":round(paid/len(payments),2) if payments else 0,"receipts":len(receipts),"payments":len(payments),"charges":len(charges),"payables":len(payables)},"by_month":[{"label":k,"value":v} for k,v in month.items()],"expense_by_month":[{"label":k,"value":v} for k,v in expense_month.items()],"result_by_month":[{"label":k,"value":v} for k,v in result_month.items()],"by_origin":[{"label":k,"value":v} for k,v in origin.items()],"by_method":[{"label":k,"value":v} for k,v in method.items()],"expense_by_category":[{"label":k,"value":v} for k,v in expense_category.items()],"payment_by_method":[{"label":k,"value":v} for k,v in payment_method.items()],"rows":receipts,"payment_rows":payments}


@router.get("/appointments")
def list_appointments(db:Session=Depends(get_db),user: User = Depends(require_module("agenda", "appointments.view"))):
    rows=db.scalars(scoped_select(db, user, Appointment).order_by(Appointment.starts_at)).all();return [{"id":x.id,"department_id":x.department_id,"title":x.title,"description":x.description,"starts_at":x.starts_at,"ends_at":x.ends_at,"kind":x.kind,"status":x.status,"order_id":x.order_id,"customer_id":x.customer_id,"employee_id":x.employee_id,"service_id":x.service_id} for x in rows]


@router.post("/appointments",status_code=201)
def create_appointment(data:AppointmentIn,db:Session=Depends(get_db),user: User = Depends(require_module("agenda", "appointments.create"))):
    if data.ends_at<=data.starts_at:raise HTTPException(400,"O término deve ser posterior ao início")
    obj=Appointment(**validate_appointment_values(db,user,data.model_dump()));db.add(obj);db.commit();return {"id":obj.id}

@router.put("/appointments/{appointment_id}")
def update_appointment(appointment_id:int,data:AppointmentIn,db:Session=Depends(get_db),user: User = Depends(require_module("agenda", "appointments.update"))):
    if data.ends_at<=data.starts_at: raise HTTPException(400,"O término deve ser posterior ao início")
    obj=scoped_get(db,user,Appointment,appointment_id)
    if not obj: raise HTTPException(404,"Agendamento não encontrado")
    for key,value in validate_appointment_values(db,user,data.model_dump(),obj).items(): setattr(obj,key,value)
    db.commit();return {"message":"Agendamento atualizado"}

@router.patch("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id:int,db:Session=Depends(get_db),user: User = Depends(require_module("agenda", "appointments.cancel"))):
    obj=scoped_get(db,user,Appointment,appointment_id)
    if not obj: raise HTTPException(404,"Agendamento não encontrado")
    obj.status="Cancelado";db.commit();return {"message":"Agendamento cancelado"}


@router.get("/notifications")
def notifications(db:Session=Depends(get_db),user:User=Depends(current_user)):
    modules=configured_modules(db);grants=permissions_for(user,modules);rows=db.scalars(select(Notification).where(Notification.user_id==user.id).order_by(Notification.created_at.desc())).all()
    notification_access={"financial":(("/financeiro",),"financial.view"),"agenda":(("/agenda",),"appointments.view"),"plans":(("/planos","/contratos","/assinaturas","/renovacoes"),"plans.view"),"academy":(("/academia","/avaliacoes","/modalidades"),"gym_students.view"),"school":(("/escola","/responsaveis","/turmas","/cursos","/coordenacao","/documentos"),"students.view")}
    hidden=[prefix for key,(prefixes,permission) in notification_access.items() if not modules[key] or permission not in grants for prefix in prefixes]
    if not modules["plans"] or "contracts.view" not in grants: hidden.extend(("/contratos","/assinaturas","/renovacoes"))
    segment_allowed=(modules["academy"] and "gym_students.view" in grants) or (modules["school"] and "students.view" in grants)
    if not segment_allowed: hidden.extend(("/alunos","/matriculas","/professores"))
    rows=[x for x in rows if not x.link or not any(x.link.startswith(prefix) for prefix in hidden)];return [{"id":x.id,"title":x.title,"message":x.message,"read":x.read,"created_at":x.created_at,"link":x.link} for x in rows]


@router.patch("/notifications/{notification_id}/read")
def read_notification(notification_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=db.get(Notification,notification_id)
    if not obj or obj.user_id!=user.id:raise HTTPException(404,"Notificação não encontrada")
    obj.read=True;db.commit();return {"message":"Notificação lida"}


@router.get("/audit")
def audit(db:Session=Depends(get_db),user: User = Depends(require_permission("audit.view"))):
    rows=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all();return [{"id":x.id,"action":x.action,"entity":x.entity,"entity_id":x.entity_id,"details":x.details,"created_at":x.created_at} for x in rows]


@router.get("/settings")
def get_settings(db:Session=Depends(get_db),user: User = Depends(require_permission("settings.manage"))):
    x=db.scalar(select(CompanySettings));return {"company_name":x.company_name,"segment":x.segment,"primary_color":x.primary_color,"terms":x.terms,"modules":configured_modules(db)}

@router.get("/settings/modules")
def get_modules(db:Session=Depends(get_db),user:User=Depends(current_user)):
    context=authorization(user,db)
    available=[permission for module,items in PERMISSIONS.items() if context["modules"][module] for permission in items] if has_permission(user,"permissions.manage",db) else []
    return {"modules":context["modules"],"permissions":context["permissions"],"available_permissions":available}

@router.put("/settings/modules")
def put_modules(data:ModulesIn,db:Session=Depends(get_db),user:User=Depends(require_permission("modules.manage"))):
    settings=db.scalar(select(CompanySettings));before=configured_modules(db);settings.modules=validate_modules(data.model_dump())
    changed=[key for key in settings.modules if before.get(key)!=settings.modules[key]]
    if changed: db.add(Notification(user_id=user.id,title="Módulos atualizados",message="Configuração alterada: "+", ".join(changed),link="/configuracoes"))
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="modules",entity_id=str(settings.id),details={"before":before,"after":settings.modules}));db.commit()
    return {"message":"Configurações atualizadas com sucesso.","modules":settings.modules,"permissions":permissions_for(user,settings.modules)}


@router.put("/settings")
def put_settings(data:SettingsIn,db:Session=Depends(get_db),user:User=Depends(require_permission("settings.manage"))):
    x=db.scalar(select(CompanySettings));before_modules=configured_modules(db);old={"company_name":x.company_name,"segment":x.segment,"primary_color":x.primary_color,"terms":x.terms,"modules":before_modules};
    values=data.model_dump();values["modules"]=validate_modules(values["modules"])
    if values["modules"] != before_modules and not has_permission(user,"modules.manage",db): raise HTTPException(403,"Sem permissão para alterar módulos.")
    for key,value in values.items():setattr(x,key,value)
    changed=[key for key in values["modules"] if before_modules.get(key)!=values["modules"][key]]
    if changed: db.add(Notification(user_id=user.id,title="Módulos atualizados",message="Configuração alterada: "+", ".join(changed),link="/configuracoes"))
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="settings",entity_id=str(x.id),details={"before":old,"after":values}));db.commit();return {"message":"Configurações salvas"}


@router.get("/search")
def global_search(q:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if len(q.strip())<2:return []
    modules=configured_modules(db);grants=permissions_for(user,modules);pattern=f"%{q.strip()}%";orders=db.scalars(scoped_select(db, user, Order).where(Order.number.ilike(pattern)|Order.title.ilike(pattern)).limit(8)).all() if "orders.view" in grants else [];customers=db.scalars(scoped_select(db, user, Customer).where(Customer.name.ilike(pattern)|Customer.document.ilike(pattern)|Customer.phone.ilike(pattern)).limit(8)).all() if set(grants)&{"customers.view","students.view","gym_students.view"} else [];employees=db.scalars(scoped_select(db, user, Employee).where(Employee.name.ilike(pattern),Employee.deleted_at.is_(None)).limit(6)).all() if set(grants)&{"employees.view","employees.view_department"} else [];services=db.scalars(scoped_select(db, user, Service).where(Service.name.ilike(pattern),Service.deleted_at.is_(None)).limit(6)).all() if "services.view" in grants else [];result=[{"type":"order","id":x.id,"title":x.number,"subtitle":x.title} for x in orders]+[{"type":"student" if modules["academy"] or modules["school"] else "customer","id":x.id,"title":x.name,"subtitle":x.phone or x.document} for x in customers]+[{"type":"teacher" if modules["academy"] or modules["school"] else "employee","id":x.id,"title":x.name,"subtitle":x.job_title} for x in employees]+[{"type":"service","id":x.id,"title":x.name,"subtitle":x.category} for x in services]
    if modules["plans"] and "plans.view" in grants: result += [{"type":"plan","id":x.id,"title":x.name,"subtitle":x.periodicity} for x in db.scalars(scoped_select(db, user, Plan).where(Plan.name.ilike(pattern)).limit(5)).all()]
    if modules["financial"] and "financial.view" in grants: result += [{"type":"payment","id":x.id,"title":f"Pagamento #{x.id}","subtitle":x.status} for x in db.scalars(scoped_select(db, user, Payment).where(Payment.status.ilike(pattern)).limit(5)).all()]
    search_access={"financial":("financial.view",),"plans":("plans.view","contracts.view"),"academy":("gym_students.view","gym_enrollments.manage","physical_assessments.manage"),"school":("students.view","classes.view","classes.manage","enrollments.manage")}
    active_modules=[key for key,permissions in search_access.items() if modules[key] and any(permission in grants for permission in permissions)]
    if active_modules:
        records=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module.in_(active_modules),ModuleRecord.deleted_at.is_(None))).all()
        record_access={("financial","charges"):"financial.view",("plans","contracts"):"contracts.view",("plans","subscriptions"):"contracts.view",("plans","renewals"):"contracts.view",("academy","enrollments"):"gym_enrollments.manage",("academy","assessments"):"physical_assessments.manage",("academy","modalities"):"gym_students.view",("school","guardians"):"students.view",("school","enrollments"):"enrollments.manage",("school","classes"):"classes.view",("school","courses"):"classes.view",("school","coordination"):"classes.manage",("school","documents"):"students.view"}
        result += [{"type":x.resource,"id":x.id,"title":str(x.data.get("name") or x.data.get("student") or x.data.get("title") or f"Registro #{x.id}"),"subtitle":x.resource} for x in records if record_access.get((x.module,x.resource)) in grants and q.strip().casefold() in str(x.data).casefold()][:12]
    return result


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), user: User = Depends(current_user)):
    grants = set(permissions_for(user, configured_modules(db)))
    can_order = bool(grants & {"orders.view", "orders.create", "orders.update", "appointments.create", "appointments.update"})
    can_services = can_order or "services.view" in grants
    can_employees = can_order or bool(grants & {"employees.view", "employees.view_department", "users.manage"})
    return {
        "services": [{"id": x.id, "name": x.name, "price": x.price if "financial.view" in grants else None}
                     for x in db.scalars(scoped_select(db, user, Service)).all()] if can_services else [],
        "employees": [{"id": x.id, "name": x.name, "department_id": x.department_id, "job_position_id": x.job_position_id}
                      for x in db.scalars(scoped_select(db, user, Employee)).all()] if can_employees else [],
        "departments": [{"id": x.id, "name": x.name} for x in db.scalars(scoped_select(db, user, Department)).all()]
    }


@router.post("/departments",status_code=201)
def create_department(name:str,db:Session=Depends(get_db),user:User=Depends(require_permission("departments.manage"))):
    x=Department(name=name);db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="department",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/meta")
def meta(user: User = Depends(current_user)):
    return {"order_statuses":[x.value for x in OrderStatus],"priorities":["Baixa","Normal","Alta","Urgente","Crítica"],"payment_statuses":["Pendente","Pago","Parcial","Atrasado","Cancelado"],"payment_methods":["PIX","Dinheiro","Cartão de crédito","Cartão de débito","Boleto","Transferência"]}


@router.get("/reports")
def reports(db:Session=Depends(get_db),user:User=Depends(require_any_permission("reports.view","reports.view_department"))):
    by_status=db.execute(select(Order.status,func.count(Order.id)).where(Order.deleted_at.is_(None),scope_clause(db,user,Order)).group_by(Order.status)).all()
    by_service=db.execute(select(Service.name,func.count(Order.id)).join(Order,Order.service_id==Service.id).where(Order.deleted_at.is_(None),scope_clause(db,user,Order)).group_by(Service.name).order_by(func.count(Order.id).desc()).limit(10)).all()
    by_employee=db.execute(select(Employee.name,func.count(Order.id)).join(Order,Order.assignee_id==Employee.id).where(Order.deleted_at.is_(None),scope_clause(db,user,Order)).group_by(Employee.name).order_by(func.count(Order.id).desc())).all()
    modules=configured_modules(db);grants=permissions_for(user,modules);total_orders=db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(Order.deleted_at.is_(None))) or 0;on_time=db.scalar(select(func.count(Order.id)).where(scope_clause(db, user, Order)).where(Order.deleted_at.is_(None),Order.status==OrderStatus.DONE)) or 0;core={"by_status":[{"label":s.value,"value":c} for s,c in by_status],"by_service":[{"label":n,"value":c} for n,c in by_service],"by_employee":[{"label":n,"value":c} for n,c in by_employee],"overview":{"orders":total_orders,"customers":db.scalar(select(func.count(Customer.id)).where(scope_clause(db, user, Customer)).where(Customer.deleted_at.is_(None))) or 0,"sla":round(on_time/total_orders*100,1) if total_orders else 0}};result={"modules":modules,"core":core,**core}
    if modules["financial"] and "financial.view" in grants:
        charges=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="financial",ModuleRecord.resource=="charges",ModuleRecord.deleted_at.is_(None))).all();payment_count=(db.scalar(select(func.count(Payment.id)).where(scope_clause(db, user, Payment))) or 0)+len(charges);received=(db.scalar(select(func.sum(Payment.amount)).where(scope_clause(db, user, Payment)).where(Payment.status=="Pago")) or 0)+sum(float(x.data.get("amount",0) or 0) for x in charges if x.status=="Pago");result["financial"]={"received":received,"pending":(db.scalar(select(func.sum(Payment.amount)).where(scope_clause(db, user, Payment)).where(Payment.status.in_(["Pendente","Atrasado"]))) or 0)+sum(float(x.data.get("amount",0) or 0) for x in charges if x.status in ("Pendente","Atrasado")),"payments":payment_count,"average_ticket":round(received/payment_count,2) if payment_count else 0}
    else: result["financial"]=None
    result["agenda"]={"appointments":db.scalar(select(func.count(Appointment.id)).where(scope_clause(db, user, Appointment))) or 0,"canceled":db.scalar(select(func.count(Appointment.id)).where(scope_clause(db, user, Appointment)).where(Appointment.status=="Cancelado")) or 0,"no_show":db.scalar(select(func.count(Appointment.id)).where(scope_clause(db, user, Appointment)).where(Appointment.status=="Não compareceu")) or 0} if modules["agenda"] else None
    if "appointments.view" not in grants: result["agenda"]=None
    result["plans"]={"plans":db.scalar(select(func.count(Plan.id)).where(scope_clause(db, user, Plan)).where(Plan.active.is_(True))) or 0} if modules["plans"] and "plans.view" in grants else None
    if result["plans"] is not None and "contracts.view" in grants: result["plans"].update({resource:db.scalar(select(func.count(ModuleRecord.id)).where(scope_clause(db, user, ModuleRecord)).where(ModuleRecord.module=="plans",ModuleRecord.resource==resource,ModuleRecord.deleted_at.is_(None))) or 0 for resource in MODULE_RESOURCES["plans"]})
    if modules["academy"] and "gym_students.view" in grants:
        result["academy"]={"students":core["overview"]["customers"]}
        if "gym_enrollments.manage" in grants: result["academy"].update({"enrollments":db.scalar(select(func.count(ModuleRecord.id)).where(scope_clause(db, user, ModuleRecord)).where(ModuleRecord.module=="academy",ModuleRecord.resource=="enrollments",ModuleRecord.deleted_at.is_(None))) or 0,"plans":db.scalar(select(func.count(Plan.id)).where(scope_clause(db, user, Plan)).where(Plan.active.is_(True))) or 0 if modules["plans"] else 0})
        if "physical_assessments.manage" in grants: result["academy"]["assessments"]=db.scalar(select(func.count(ModuleRecord.id)).where(scope_clause(db, user, ModuleRecord)).where(ModuleRecord.module=="academy",ModuleRecord.resource=="assessments",ModuleRecord.deleted_at.is_(None))) or 0
    else: result["academy"]=None
    if modules["school"] and "students.view" in grants:
        result["school"]={"students":core["overview"]["customers"]}
        if "enrollments.manage" in grants: result["school"]["enrollments"]=db.scalar(select(func.count(ModuleRecord.id)).where(scope_clause(db, user, ModuleRecord)).where(ModuleRecord.module=="school",ModuleRecord.resource=="enrollments",ModuleRecord.deleted_at.is_(None))) or 0
        if "classes.view" in grants:
            classes=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="school",ModuleRecord.resource=="classes",ModuleRecord.deleted_at.is_(None))).all();capacity=sum(int(x.data.get("capacity",0) or 0) for x in classes);occupied=sum(int(x.data.get("students",0) or 0) for x in classes)
            result["school"].update({"courses":db.scalar(select(func.count(ModuleRecord.id)).where(scope_clause(db, user, ModuleRecord)).where(ModuleRecord.module=="school",ModuleRecord.resource=="courses",ModuleRecord.deleted_at.is_(None))) or 0,"classes":len(classes),"occupancy":f"{round(occupied/capacity*100,1) if capacity else 0}%"})
    else: result["school"]=None
    return result


@router.get("/reports/orders.csv")
def export_orders(db:Session=Depends(get_db),user:User=Depends(require_permission("reports.export"))):
    if not has_permission(user,"orders.view",db): raise HTTPException(403,"É necessária permissão para consultar ordens antes de exportá-las.")
    output=io.StringIO();writer=csv.writer(output,delimiter=";");writer.writerow(["Número","Cliente","Serviço","Prioridade","Status","Previsão"])
    for x in db.scalars(scoped_select(db, user, Order).where(Order.deleted_at.is_(None)).order_by(Order.created_at.desc())).all():writer.writerow([x.number,x.customer.name,x.service.name,x.priority,x.status.value,x.due_date or ""])
    db.add(AuditLog(user_id=user.id,action="EXPORT",entity="orders"));db.commit();return StreamingResponse(iter([output.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=ordens.csv"})


@router.get("/reports/financial.csv")
def export_financial(period_from:date|None=None,period_to:date|None=None,db:Session=Depends(get_db),user:User=Depends(require_module("financial","financial.export"))):
    if not has_permission(user,"financial.view",db): raise HTTPException(403,"É necessária permissão para consultar o financeiro antes de exportá-lo.")
    def cell(value):
        text = "" if value is None else str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text
    data=financial_reports(period_from,period_to,db,user)
    output=io.StringIO();output.write("\ufeff");writer=csv.writer(output,delimiter=";")
    writer.writerow(["Tipo","Data","Pessoa","Categoria/Origem","Forma","Referencia","Valor","Observacao"])
    for row in data["rows"]:
        writer.writerow(["Recebimento",row["received_on"],cell(row["customer"]),cell(row["origin"]),cell(row["method"]),cell(row.get("reference")),row["amount"],cell(row.get("note"))])
    for row in data["payment_rows"]:
        writer.writerow(["Pagamento",row["paid_on"],cell(row["beneficiary"]),cell(row["category"]),cell(row["method"]),cell(row.get("order_id")),row["amount"],cell(row.get("note"))])
    db.add(AuditLog(user_id=user.id,action="EXPORT",entity="financial",details={"period_from":str(period_from) if period_from else None,"period_to":str(period_to) if period_to else None}));db.commit();return StreamingResponse(iter([output.getvalue()]),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=financeiro.csv"})


@router.post("/orders/{order_id}/attachments",status_code=201)
async def upload_attachment(order_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(require_permission("orders.attachments"))):
    if not scoped_get(db,user,Order,order_id):raise HTTPException(404,"Ordem não encontrada")
    allowed={"application/pdf","image/jpeg","image/png","application/vnd.openxmlformats-officedocument.wordprocessingml.document","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if file.content_type not in allowed:raise HTTPException(400,"Tipo de arquivo não permitido")
    content=await file.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise HTTPException(400,"Arquivo excede 10 MB")
    suffix=Path(file.filename or "arquivo").suffix;stored=f"{secrets.token_hex(16)}{suffix}";target=Path("uploads")/stored;target.write_bytes(content)
    obj=OrderAttachment(order_id=order_id,user_id=user.id,original_name=file.filename or "arquivo",stored_name=stored,content_type=file.content_type or "application/octet-stream",size=len(content));db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Anexo adicionado",new_value=obj.original_name));db.commit();return {"id":obj.id,"name":obj.original_name,"url":f"/api/orders/{order_id}/attachments/{obj.id}"}


@router.get("/plans")
def list_plans(db:Session=Depends(get_db),user: User = Depends(require_module("plans", "plans.view"))):
    return [{"id":x.id,"name":x.name,"description":x.description,"amount":x.amount,"periodicity":x.periodicity,"starts_on":x.starts_on,"ends_on":x.ends_on,"auto_renew":x.auto_renew,"max_users":x.max_users,"included_services":x.included_services,"status":x.status,"active":x.active} for x in db.scalars(scoped_select(db, user, Plan).order_by(Plan.name)).all()]


@router.post("/plans",status_code=201)
def create_plan(data:PlanIn,db:Session=Depends(get_db),user:User=Depends(require_module("plans", "plans.create"))):
    if data.ends_on and data.starts_on and data.ends_on<data.starts_on: raise HTTPException(422,"A data final deve ser posterior à inicial")
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id")))
    x=Plan(**values);db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="plan",entity_id=str(x.id)));db.commit();return {"id":x.id}

@router.put("/plans/{plan_id}")
def update_plan(plan_id:int,data:PlanIn,db:Session=Depends(get_db),user:User=Depends(require_module("plans", "plans.create"))):
    if data.ends_on and data.starts_on and data.ends_on<data.starts_on: raise HTTPException(422,"A data final deve ser posterior à inicial")
    x=scoped_get(db,user,Plan,plan_id)
    if not x: raise HTTPException(404,"Plano não encontrado")
    enforce_scope(db,user,x,write=True)
    values=data.model_dump();values.update(scope_values(db,user,values.get("department_id"),existing=x))
    for key,value in values.items(): setattr(x,key,value)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="plan",entity_id=str(x.id)));db.commit();return {"message":"Plano atualizado"}


@router.get("/module-data/{module}/{resource}")
def list_module_records(module:str,resource:str,q:str="",status:str|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
    check_resource(module,resource,db,user)
    stmt=scoped_select(db, user, ModuleRecord).where(ModuleRecord.module==module,ModuleRecord.resource==resource,ModuleRecord.deleted_at.is_(None))
    if status: stmt=stmt.where(ModuleRecord.status==status)
    rows=db.scalars(stmt.order_by(ModuleRecord.created_at.desc())).all()
    if q: rows=[x for x in rows if q.casefold() in str(x.data).casefold()]
    return [record_out(x) for x in rows]


@router.post("/module-data/{module}/{resource}",status_code=201)
def create_module_record(module:str,resource:str,payload:ModuleRecordIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    check_resource(module,resource,db,user,"create")
    missing=[field for field in REQUIRED_FIELDS.get((module,resource),set()) if payload.data.get(field) in (None,"")]
    if missing: raise HTTPException(422,f"Campos obrigatórios: {', '.join(sorted(missing))}")
    data=dict(payload.data)
    modules=configured_modules(db);grants=permissions_for(user,modules)
    if module=="plans" and resource=="subscriptions" and data.get("generate_charge"):
        if not modules["financial"]: raise HTTPException(404,"O módulo financeiro não está habilitado.")
        if "financial.create" not in grants: raise HTTPException(403,"Seu perfil não possui permissão para gerar cobranças.")
    linked_plan=None
    if module in ("academy","school") and resource=="enrollments" and data.get("plan"):
        if not modules["plans"]: raise HTTPException(404,"O módulo de planos não está habilitado.")
        if "plans.view" not in grants or "contracts.manage" not in grants: raise HTTPException(403,"Seu perfil não pode vincular planos e gerar assinaturas.")
        linked_plan=db.scalar(scoped_select(db,user,Plan).where(Plan.name==str(data["plan"]),Plan.active.is_(True)))
        if not linked_plan: raise HTTPException(422,"O plano informado não existe, está inativo ou está fora do seu escopo.")
    if module=="academy" and resource=="assessments":
        try:
            height=float(data["height"]);weight=float(data["weight"]);data["bmi"]=round(weight/(height*height),2) if height else None
        except (TypeError,ValueError): raise HTTPException(422,"Peso e altura devem ser numéricos")
    if module=="plans" and resource=="subscriptions" and modules["financial"] and data.get("generate_charge"): data["financial_status"]="Pendente"
    boundary=scope_values(db,user,payload.department_id)
    x=ModuleRecord(module=module,resource=resource,data=data,status=payload.status,**boundary);db.add(x);db.flush()
    if module=="plans" and resource=="subscriptions" and modules["financial"] and data.get("generate_charge") and data.get("amount") and data.get("next_charge"):
        db.add(ModuleRecord(module="financial",resource="charges",department_id=x.department_id,owner_user_id=x.owner_user_id,data={"customer":data.get("customer"),"amount":data.get("amount"),"due_date":data.get("next_charge"),"plan":data.get("plan"),"subscription_id":x.id},status="Pendente"))
    if module in ("academy","school") and resource=="enrollments" and modules["plans"] and data.get("plan"):
        plan=linked_plan
        start_value=str(data.get("starts_on") or data.get("date") or date.today().isoformat())
        try: start_date=date.fromisoformat(start_value)
        except ValueError: start_date=date.today()
        due_value=data.get("ends_on") or next_billing_date(start_date,plan.periodicity if plan else "Mensal").isoformat()
        can_generate=modules["financial"] and "financial.create" in grants
        subscription_data={"customer":data.get("student"),"plan":data.get("plan"),"amount":plan.amount if plan else data.get("amount"),"periodicity":plan.periodicity if plan else "Mensal","starts_on":start_date.isoformat(),"ends_on":data.get("ends_on"),"next_charge":due_value,"generate_charge":can_generate,"financial_status":"Pendente" if can_generate else None,"enrollment_id":x.id,"segment":module}
        subscription=ModuleRecord(module="plans",resource="subscriptions",data=subscription_data,status="Ativa",**boundary);db.add(subscription);db.flush()
        if can_generate and subscription_data.get("amount") and due_value:
            db.add(ModuleRecord(module="financial",resource="charges",department_id=x.department_id,owner_user_id=x.owner_user_id,data={"customer":data.get("student"),"amount":subscription_data["amount"],"due_date":due_value,"plan":data.get("plan"),"subscription_id":subscription.id,"enrollment_id":x.id},status="Pendente"))
    db.add(AuditLog(user_id=user.id,action="CREATE",entity=f"{module}.{resource}",entity_id=str(x.id)));db.commit();db.refresh(x);return record_out(x)


@router.post("/billing/generate")
def generate_recurring_charges(db:Session=Depends(get_db),user:User=Depends(require_module("financial","financial.create"))):
    modules=configured_modules(db)
    if not has_permission(user,"contracts.manage",db): raise HTTPException(403,"Sem permissão para gerenciar contratos.")
    if not modules["plans"]: raise HTTPException(404,"O modulo de planos nao esta habilitado.")
    subscriptions=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="plans",ModuleRecord.resource=="subscriptions",ModuleRecord.deleted_at.is_(None))).all()
    charges=db.scalars(scoped_select(db, user, ModuleRecord).where(ModuleRecord.module=="financial",ModuleRecord.resource=="charges",ModuleRecord.deleted_at.is_(None))).all()
    existing={(x.data.get("subscription_id"),str(x.data.get("due_date"))) for x in charges}
    generated=0
    for subscription in subscriptions:
        data=dict(subscription.data or {})
        if subscription.status!="Ativa" or not data.get("generate_charge") or not data.get("amount") or not data.get("next_charge"): continue
        try: due=date.fromisoformat(str(data["next_charge"]))
        except ValueError: continue
        cycles=0
        while due<=date.today() and cycles<120:
            key=(subscription.id,due.isoformat())
            if key not in existing:
                db.add(ModuleRecord(module="financial",resource="charges",department_id=subscription.department_id,owner_user_id=subscription.owner_user_id,data={"customer":data.get("customer"),"amount":data.get("amount"),"due_date":due.isoformat(),"plan":data.get("plan"),"subscription_id":subscription.id},status="Pendente"));existing.add(key);generated+=1
            due=next_billing_date(due,data.get("periodicity"));cycles+=1
        data["next_charge"]=due.isoformat();subscription.data=data
    db.add(AuditLog(user_id=user.id,action="GENERATE",entity="financial.charges",details={"generated":generated}));db.commit()
    return {"message":f"{generated} cobranca(s) gerada(s).","generated":generated}


@router.put("/module-data/{module}/{resource}/{record_id}")
def update_module_record(module:str,resource:str,record_id:int,payload:ModuleRecordIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    check_resource(module,resource,db,user,"update");x=scoped_get(db,user,ModuleRecord,record_id)
    if not x or x.deleted_at or x.module!=module or x.resource!=resource: raise HTTPException(404,"Registro não encontrado")
    missing=[field for field in REQUIRED_FIELDS.get((module,resource),set()) if payload.data.get(field) in (None,"")]
    if missing: raise HTTPException(422,f"Campos obrigatórios: {', '.join(sorted(missing))}")
    boundary=scope_values(db,user,payload.department_id,existing=x)
    x.department_id=boundary["department_id"]
    old_subscription_id=(x.data or {}).get("subscription_id")
    merged_data=dict(x.data or {});merged_data.update(payload.data)
    if module=="academy" and resource=="assessments":
        try:
            height=float(merged_data["height"]);weight=float(merged_data["weight"]);merged_data["bmi"]=round(weight/(height*height),2) if height else None
        except (TypeError,ValueError): raise HTTPException(422,"Peso e altura devem ser numéricos")
    if module=="financial" and resource=="charges" and merged_data.get("subscription_id"):
        modules=configured_modules(db);grants=permissions_for(user,modules);requested_link="subscription_id" in payload.data and payload.data.get("subscription_id")!=old_subscription_id
        if requested_link and (not modules["plans"] or "contracts.manage" not in grants): raise HTTPException(403,"Seu perfil não pode alterar o vínculo com a assinatura.")
        subscription=scoped_get(db,user,ModuleRecord,int(merged_data["subscription_id"])) if modules["plans"] and "contracts.manage" in grants else None
        if subscription and subscription.module=="plans" and subscription.resource=="subscriptions":
            subscription_data=dict(subscription.data or {});subscription_data["financial_status"]=payload.status
            if payload.status=="Pago": subscription_data["last_payment"]=date.today().isoformat()
            subscription.data=subscription_data
    x.data=merged_data;x.status=payload.status
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity=f"{module}.{resource}",entity_id=str(x.id)));db.commit();return record_out(x)


@router.delete("/module-data/{module}/{resource}/{record_id}")
def delete_module_record(module:str,resource:str,record_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    check_resource(module,resource,db,user,"delete");x=scoped_get(db,user,ModuleRecord,record_id)
    if not x or x.deleted_at or x.module!=module or x.resource!=resource: raise HTTPException(404,"Registro não encontrado")
    x.deleted_at=utc_now();db.add(AuditLog(user_id=user.id,action="DELETE",entity=f"{module}.{resource}",entity_id=str(x.id)));db.commit();return {"message":"Registro removido"}


@router.get("/custom-fields")
def list_custom_fields(entity:str|None=None,db:Session=Depends(get_db),user: User = Depends(current_user)):
    stmt=select(CustomField).where(CustomField.active.is_(True));stmt=stmt.where(CustomField.entity==entity) if entity else stmt;return [{"id":x.id,"entity":x.entity,"label":x.label,"field_type":x.field_type,"required":x.required,"options":x.options} for x in db.scalars(stmt).all()]


@router.post("/custom-fields",status_code=201)
def create_custom_field(data:CustomFieldIn,db:Session=Depends(get_db),user:User=Depends(require_permission("settings.manage"))):
    x=CustomField(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="custom_field",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/admin/demo-data")
def demo_summary(db: Session = Depends(get_db), user: User = Depends(require_permission("system.manage"))):
    models = [User, Customer, Department, Employee, Service, Order, OrderHistory, OrderComment, ChecklistItem, OrderTask, OrderAttachment, Appointment, Notification, Payment, FinancialReceipt, FinancialPayable, FinancialPayment, ChargeHistory, Plan, Subscription, ModuleRecord]
    counts = {m.__tablename__: db.scalar(select(func.count(m.id)).where(m.is_demo.is_(True))) or 0 for m in models}
    return {"has_demo_data": any(counts.values()), "counts": counts, "total": sum(counts.values())}


@router.delete("/admin/demo-data")
def delete_demo_data(data: DemoDelete, db: Session = Depends(get_db), user: User = Depends(require_permission("system.manage"))):
    if data.confirmation != "EXCLUIR DADOS DEMO":
        raise HTTPException(400, "Frase de confirmação incorreta")
    # Preserve qualquer cadastro demo que tenha se tornado dependência de um
    # registro real. Isso evita quebrar chaves estrangeiras e nunca apaga dados reais.
    ids = lambda statement: {value for value in db.scalars(statement).all() if value is not None}
    protected_customers = ids(select(Order.customer_id).where(Order.is_demo.is_(False))) | ids(select(Appointment.customer_id).where(Appointment.is_demo.is_(False))) | ids(select(Subscription.customer_id).where(Subscription.is_demo.is_(False))) | ids(select(FinancialReceipt.customer_id).where(FinancialReceipt.is_demo.is_(False)))
    protected_services = ids(select(Order.service_id).where(Order.is_demo.is_(False))) | ids(select(Appointment.service_id).where(Appointment.is_demo.is_(False)))
    protected_employees = ids(select(User.employee_id).where(or_(User.is_demo.is_(False), User.id == user.id))) | ids(select(Order.assignee_id).where(Order.is_demo.is_(False))) | ids(select(OrderTask.assignee_id).where(OrderTask.is_demo.is_(False))) | ids(select(Appointment.employee_id).where(Appointment.is_demo.is_(False)))
    protected_plans = ids(select(Subscription.plan_id).where(Subscription.is_demo.is_(False)))
    protected_departments = ids(select(JobPosition.department_id)) | ids(select(User.department_id).where(or_(User.is_demo.is_(False), User.id == user.id))) | ids(select(Employee.department_id).where(Employee.id.in_(protected_employees)))
    protected_departments |= ids(select(Customer.department_id).where(Customer.id.in_(protected_customers))) | ids(select(Service.department_id).where(Service.id.in_(protected_services))) | ids(select(Plan.department_id).where(Plan.id.in_(protected_plans)))
    for model in (Customer, Service, Plan, ModuleRecord, Appointment, FinancialReceipt, FinancialPayable, FinancialPayment):
        protected_departments |= ids(select(model.department_id).where(model.is_demo.is_(False)))
    protected_departments |= ids(select(Order.department_id).where(Order.is_demo.is_(False)))

    protected_users = {user.id}
    protected_users |= ids(select(Customer.owner_user_id).where(Customer.id.in_(protected_customers))) | ids(select(Service.owner_user_id).where(Service.id.in_(protected_services))) | ids(select(Plan.owner_user_id).where(Plan.id.in_(protected_plans)))
    for model in (Customer, Service, Plan, ModuleRecord, Appointment, FinancialReceipt, FinancialPayable, FinancialPayment):
        protected_users |= ids(select(model.owner_user_id).where(model.is_demo.is_(False)))
    protected_users |= ids(select(OrderComment.user_id).where(OrderComment.is_demo.is_(False)))
    protected_users |= ids(select(OrderAttachment.user_id).where(OrderAttachment.is_demo.is_(False)))

    # Dependentes primeiro; tudo acontece em uma única transação.
    counts = {}
    protected_by_model = {Employee: protected_employees, Service: protected_services, Customer: protected_customers, Department: protected_departments, Plan: protected_plans}
    for model in [FinancialPayment, FinancialReceipt, FinancialPayable, ChargeHistory, Payment, OrderAttachment, OrderComment, ChecklistItem, OrderTask, Appointment, Notification, OrderHistory, ModuleRecord, Subscription, Order]:
        items = db.scalars(select(model).where(model.is_demo.is_(True))).all()
        counts[model.__tablename__] = len(items)
        for item in items: db.delete(item)
    db.flush()

    demo_users = db.scalars(select(User).where(User.is_demo.is_(True), User.id.not_in(protected_users))).all()
    deleting_user_ids = [item.id for item in demo_users]
    if deleting_user_ids:
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(deleting_user_ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.user_id.in_(deleting_user_ids)).update({"user_id": None}, synchronize_session=False)
        db.query(OrderHistory).filter(OrderHistory.user_id.in_(deleting_user_ids)).update({"user_id": None}, synchronize_session=False)
    counts["users"] = len(demo_users)
    for item in demo_users: db.delete(item)
    db.flush()

    for model in [Employee, Service, Customer, Department, Plan]:
        protected = protected_by_model[model]
        query = select(model).where(model.is_demo.is_(True))
        if protected: query = query.where(model.id.not_in(protected))
        items = db.scalars(query).all()
        counts[model.__tablename__] = len(items)
        for item in items: db.delete(item)
    # Registros preservados deixam de ser demo; a próxima limpeza não os remove.
    for model, protected in protected_by_model.items():
        if protected:
            for item in db.scalars(select(model).where(model.id.in_(protected), model.is_demo.is_(True))).all(): item.is_demo = False
    for item in db.scalars(select(User).where(User.id.in_(protected_users), User.is_demo.is_(True))).all(): item.is_demo = False
    db.add(AuditLog(user_id=user.id, action="DELETE_DEMO_DATA", entity="system", details=counts))
    db.commit()
    return {"message": "Dados demonstrativos removidos permanentemente", "deleted": counts}

@router.patch("/orders/{order_id}")
def patch_order(order_id: int, changes: dict, db: Session = Depends(get_db), user: User = Depends(require_permission("orders.update"))):
    existing = scoped_get(db, user, Order, order_id, write=True)
    permitted = set(OrderIn.model_fields)
    if set(changes) - permitted:
        raise HTTPException(422, "Campos não permitidos na alteração da ordem.")
    values = {name: getattr(existing, name) for name in permitted}
    values.update(changes)
    try:
        data = OrderIn.model_validate(values)
    except ValueError as error:
        raise HTTPException(422, "Dados da ordem inválidos.") from error
    return update_order(order_id, data, db, user)


@router.get("/orders/{order_id}/attachments/{attachment_id}")
def download_attachment(order_id: int, attachment_id: int, db: Session = Depends(get_db), user: User = Depends(require_permission("orders.view"))):
    scoped_get(db, user, Order, order_id)
    attachment = db.get(OrderAttachment, attachment_id)
    if not attachment or attachment.order_id != order_id:
        raise HTTPException(404, "Anexo não encontrado.")
    uploads = Path("uploads").resolve()
    target = (uploads / attachment.stored_name).resolve()
    if not target.is_relative_to(uploads) or not target.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(target, filename=attachment.original_name, media_type=attachment.content_type)

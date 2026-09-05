from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from fastapi.responses import StreamingResponse
from pathlib import Path
import csv,io,secrets
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import get_db
from .models import AuditLog, Appointment, ChecklistItem, CompanySettings, CustomField, Customer, Department, Employee, Notification, Order, OrderAttachment, OrderComment, OrderHistory, OrderStatus, OrderTask, Payment, Plan, RefreshToken, Role, Service, User
from .schemas import AppointmentIn, ChangePassword, ChecklistIn, CommentIn, CustomFieldIn, CustomerIn, CustomerOut, DemoDelete, EmployeeIn, Login, OrderIn, PaymentIn, PlanIn, ServiceIn, SettingsIn, TaskIn, Token, UserIn
from .security import admin_only, create_refresh_token, create_token, current_user, hash_password, manage_finance, manage_records, operate_orders, verify_password
import hashlib
from datetime import timedelta

router = APIRouter(prefix="/api")


@router.post("/auth/login", response_model=Token)
def login(data: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "E-mail ou senha inválidos")
    user.last_login = datetime.utcnow();raw,digest=create_refresh_token();db.add(RefreshToken(user_id=user.id,token_hash=digest,expires_at=datetime.utcnow()+timedelta(days=30)));db.commit()
    return Token(access_token=create_token(user),refresh_token=raw,user={"id": user.id, "name": user.name, "email": user.email, "role": user.role.value})


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role.value}


@router.post("/auth/refresh",response_model=Token)
def refresh(refresh_token:str,db:Session=Depends(get_db)):
    digest=hashlib.sha256(refresh_token.encode()).hexdigest();stored=db.scalar(select(RefreshToken).where(RefreshToken.token_hash==digest,RefreshToken.revoked_at.is_(None),RefreshToken.expires_at>datetime.utcnow()))
    if not stored:raise HTTPException(401,"Refresh token inválido")
    user=db.get(User,stored.user_id);stored.revoked_at=datetime.utcnow();raw,new_digest=create_refresh_token();db.add(RefreshToken(user_id=user.id,token_hash=new_digest,expires_at=datetime.utcnow()+timedelta(days=30)));db.commit();return Token(access_token=create_token(user),refresh_token=raw,user={"id":user.id,"name":user.name,"email":user.email,"role":user.role.value})


@router.post("/auth/change-password")
def change_password(data:ChangePassword,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not verify_password(data.current_password,user.password_hash):raise HTTPException(400,"Senha atual incorreta")
    user.password_hash=hash_password(data.new_password);db.query(RefreshToken).filter(RefreshToken.user_id==user.id,RefreshToken.revoked_at.is_(None)).update({"revoked_at":datetime.utcnow()});db.commit();return {"message":"Senha alterada"}


@router.post("/auth/forgot-password")
def forgot_password(email:str):
    return {"message":"Se o e-mail estiver cadastrado, as instruções serão enviadas."}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(current_user)):
    active = Order.deleted_at.is_(None)
    total = db.scalar(select(func.count(Order.id)).where(active)) or 0
    done = db.scalar(select(func.count(Order.id)).where(active, Order.status == OrderStatus.DONE)) or 0
    open_count = db.scalar(select(func.count(Order.id)).where(active, Order.status.in_([OrderStatus.OPEN, OrderStatus.ANALYSIS]))) or 0
    progress = db.scalar(select(func.count(Order.id)).where(active, Order.status == OrderStatus.PROGRESS)) or 0
    overdue = db.scalar(select(func.count(Order.id)).where(active, Order.due_date < date.today(), Order.status.notin_([OrderStatus.DONE, OrderStatus.CANCELED]))) or 0
    revenue = db.scalar(select(func.sum(Payment.amount)).where(Payment.status == "Pago")) or 0
    pending = db.scalar(select(func.sum(Payment.amount)).where(Payment.status.in_(["Pendente", "Atrasado"]))) or 0
    return {"orders": {"total": total, "open": open_count, "progress": progress, "done": done, "overdue": overdue}, "customers": db.scalar(select(func.count(Customer.id)).where(Customer.deleted_at.is_(None))) or 0, "revenue": revenue, "pending": pending, "completion_rate": round(done / total * 100, 1) if total else 0}


@router.get("/customers", response_model=list[CustomerOut])
def customers(q: str = "", status: str | None = None, city: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(Customer).where(Customer.deleted_at.is_(None))
    if q: stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    if status: stmt = stmt.where(Customer.status == status)
    if city: stmt = stmt.where(Customer.city.ilike(f"%{city}%"))
    return db.scalars(stmt.order_by(Customer.created_at.desc())).all()


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(data: CustomerIn, db: Session = Depends(get_db), user: User = Depends(manage_records)):
    obj = Customer(**data.model_dump()); db.add(obj); db.flush()
    db.add(AuditLog(user_id=user.id, action="CREATE", entity="customer", entity_id=str(obj.id)))
    db.commit(); db.refresh(obj); return obj


@router.get("/customers/{customer_id}")
def customer_detail(customer_id:int,db:Session=Depends(get_db),_:User=Depends(current_user)):
    x=db.get(Customer,customer_id)
    if not x or x.deleted_at:raise HTTPException(404,"Cliente não encontrado")
    orders=db.scalars(select(Order).where(Order.customer_id==x.id,Order.deleted_at.is_(None)).order_by(Order.created_at.desc())).all()
    return {"id":x.id,"name":x.name,"document":x.document,"phone":x.phone,"email":x.email,"city":x.city,"state":x.state,"status":x.status,"notes":x.notes,"created_at":x.created_at,"orders":[{"id":o.id,"number":o.number,"title":o.title,"status":o.status.value,"created_at":o.created_at} for o in orders]}


@router.put("/customers/{customer_id}",response_model=CustomerOut)
def update_customer(customer_id:int,data:CustomerIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=db.get(Customer,customer_id)
    if not x or x.deleted_at:raise HTTPException(404,"Cliente não encontrado")
    for k,v in data.model_dump().items():setattr(x,k,v)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="customer",entity_id=str(x.id)));db.commit();db.refresh(x);return x


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=db.get(Customer,customer_id)
    if not x:raise HTTPException(404,"Cliente não encontrado")
    x.deleted_at=datetime.utcnow();db.add(AuditLog(user_id=user.id,action="DELETE",entity="customer",entity_id=str(x.id)));db.commit();return {"message":"Cliente movido para a lixeira"}


@router.get("/orders")
def orders(status: str | None = None, priority: str | None = None, customer_id: int | None = None, assignee_id: int | None = None, service_id: int | None = None, department_id: int | None = None, overdue: bool = False, q: str = "", db: Session = Depends(get_db), _: User = Depends(current_user)):
    stmt = select(Order).where(Order.deleted_at.is_(None))
    if status: stmt = stmt.where(Order.status == status)
    if priority: stmt = stmt.where(Order.priority == priority)
    if customer_id: stmt = stmt.where(Order.customer_id == customer_id)
    if assignee_id: stmt = stmt.where(Order.assignee_id == assignee_id)
    if service_id: stmt = stmt.where(Order.service_id == service_id)
    if department_id: stmt = stmt.where(Order.department_id == department_id)
    if overdue: stmt = stmt.where(Order.due_date < date.today(), Order.status.notin_([OrderStatus.DONE,OrderStatus.CANCELED]))
    if q: stmt = stmt.where(Order.number.ilike(f"%{q}%") | Order.title.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(Order.created_at.desc())).all()
    return [{"id": x.id, "number": x.number, "title": x.title, "customer": x.customer.name, "service": x.service.name, "assignee": x.assignee.name if x.assignee else None, "priority": x.priority, "status": x.status.value, "due_date": x.due_date, "value": x.value} for x in rows]


@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    x = db.get(Order, order_id)
    if not x or x.deleted_at: raise HTTPException(404, "Ordem não encontrada")
    comments = db.scalars(select(OrderComment).where(OrderComment.order_id == x.id).order_by(OrderComment.created_at.desc())).all()
    checklist = db.scalars(select(ChecklistItem).where(ChecklistItem.order_id == x.id).order_by(ChecklistItem.position)).all()
    tasks = db.scalars(select(OrderTask).where(OrderTask.order_id == x.id)).all()
    history = db.scalars(select(OrderHistory).where(OrderHistory.order_id == x.id).order_by(OrderHistory.created_at.desc())).all()
    payments = db.scalars(select(Payment).where(Payment.order_id == x.id)).all()
    attachments=db.scalars(select(OrderAttachment).where(OrderAttachment.order_id==x.id)).all()
    return {"id":x.id,"number":x.number,"title":x.title,"description":x.description,"priority":x.priority,"status":x.status.value,"due_date":x.due_date,"value":x.value,"customer":{"id":x.customer.id,"name":x.customer.name,"phone":x.customer.phone},"service":{"id":x.service.id,"name":x.service.name},"assignee":{"id":x.assignee.id,"name":x.assignee.name} if x.assignee else None,"comments":[{"id":c.id,"content":c.content,"internal":c.internal,"author":c.user.name,"created_at":c.created_at} for c in comments],"checklist":[{"id":i.id,"title":i.title,"completed":i.completed} for i in checklist],"tasks":[{"id":t.id,"title":t.title,"status":t.status,"priority":t.priority,"due_date":t.due_date,"assignee":t.assignee.name if t.assignee else None} for t in tasks],"history":[{"id":h.id,"action":h.action,"old_value":h.old_value,"new_value":h.new_value,"created_at":h.created_at} for h in history],"payments":[{"id":p.id,"amount":p.amount,"status":p.status,"method":p.method,"due_date":p.due_date} for p in payments],"attachments":[{"id":a.id,"name":a.original_name,"size":a.size,"url":f"/uploads/{a.stored_name}"} for a in attachments]}


@router.post("/orders", status_code=201)
def create_order(data: OrderIn, db: Session = Depends(get_db), user: User = Depends(operate_orders)):
    last = db.scalar(select(func.max(Order.id))) or 0
    obj = Order(**data.model_dump(), number=f"OS-{datetime.now().year}{last+1:04d}")
    db.add(obj); db.flush(); db.add(OrderHistory(order_id=obj.id, user_id=user.id, action="Ordem criada", new_value=obj.status.value))
    db.add(AuditLog(user_id=user.id, action="CREATE", entity="order", entity_id=str(obj.id))); db.commit()
    return {"id": obj.id, "number": obj.number}


@router.put("/orders/{order_id}")
def update_order(order_id:int,data:OrderIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=db.get(Order,order_id)
    if not x or x.deleted_at:raise HTTPException(404,"Ordem não encontrada")
    before={"status":x.status.value,"title":x.title}
    for k,v in data.model_dump().items():
        if k=="department_id" and v is None: continue
        setattr(x,k,v)
    db.add(OrderHistory(order_id=x.id,user_id=user.id,action="Ordem atualizada",old_value=str(before),new_value=x.title));db.add(AuditLog(user_id=user.id,action="UPDATE",entity="order",entity_id=str(x.id)));db.commit();return {"message":"Ordem atualizada"}


@router.delete("/orders/{order_id}")
def delete_order(order_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=db.get(Order,order_id)
    if not x:raise HTTPException(404,"Ordem não encontrada")
    x.deleted_at=datetime.utcnow();db.add(AuditLog(user_id=user.id,action="DELETE",entity="order",entity_id=str(x.id)));db.commit();return {"message":"Ordem movida para a lixeira"}


@router.patch("/orders/{order_id}/status")
def update_status(order_id: int, status: OrderStatus, db: Session = Depends(get_db), user: User = Depends(operate_orders)):
    obj = db.get(Order, order_id)
    if not obj or obj.deleted_at: raise HTTPException(404, "Ordem não encontrada")
    old = obj.status.value; obj.status = status
    if status == OrderStatus.DONE: obj.completed_at = datetime.utcnow()
    db.add(OrderHistory(order_id=obj.id, user_id=user.id, action="Status alterado", old_value=old, new_value=status.value))
    db.add(AuditLog(user_id=user.id, action="STATUS_CHANGE", entity="order", entity_id=str(obj.id), details={"from": old, "to": status.value})); db.commit()
    return {"message": "Status atualizado"}


@router.post("/orders/{order_id}/comments", status_code=201)
def add_comment(order_id:int, data:CommentIn, db:Session=Depends(get_db), user:User=Depends(current_user)):
    if not db.get(Order,order_id): raise HTTPException(404,"Ordem não encontrada")
    obj=OrderComment(order_id=order_id,user_id=user.id,**data.model_dump());db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Comentário adicionado"));db.commit();return {"id":obj.id}


@router.post("/orders/{order_id}/checklist", status_code=201)
def add_checklist(order_id:int,data:ChecklistIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=ChecklistItem(order_id=order_id,title=data.title);db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Item de checklist adicionado",new_value=data.title));db.commit();return {"id":obj.id}


@router.patch("/orders/{order_id}/checklist/{item_id}")
def toggle_checklist(order_id:int,item_id:int,completed:bool,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=db.get(ChecklistItem,item_id)
    if not obj or obj.order_id!=order_id:raise HTTPException(404,"Item não encontrado")
    obj.completed=completed;db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Checklist atualizado",new_value=obj.title));db.commit();return {"completed":completed}


@router.post("/orders/{order_id}/tasks", status_code=201)
def add_task(order_id:int,data:TaskIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=OrderTask(order_id=order_id,**data.model_dump());db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Subtarefa criada",new_value=data.title));db.commit();return {"id":obj.id}


@router.get("/employees")
def list_employees(q:str="",status:str|None=None,department_id:int|None=None,job_title:str|None=None,db:Session=Depends(get_db),_:User=Depends(current_user)):
    stmt=select(Employee).where(Employee.deleted_at.is_(None))
    if q:stmt=stmt.where(Employee.name.ilike(f"%{q}%"))
    if status:stmt=stmt.where(Employee.status==status)
    if department_id:stmt=stmt.where(Employee.department_id==department_id)
    if job_title:stmt=stmt.where(Employee.job_title.ilike(f"%{job_title}%"))
    rows=db.scalars(stmt.order_by(Employee.name)).all();return [{"id":x.id,"name":x.name,"job_title":x.job_title,"email":x.email,"phone":x.phone,"status":x.status,"department_id":x.department_id,"department":x.department.name if x.department else None} for x in rows]


@router.post("/employees",status_code=201)
def create_employee(data:EmployeeIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=Employee(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="employee",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.put("/employees/{employee_id}")
def update_employee(employee_id:int,data:EmployeeIn,db:Session=Depends(get_db),user:User=Depends(manage_records)):
    x=db.get(Employee,employee_id)
    if not x or x.deleted_at:raise HTTPException(404,"Funcionário não encontrado")
    for k,v in data.model_dump().items():setattr(x,k,v)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="employee",entity_id=str(x.id)));db.commit();return {"message":"Funcionário atualizado"}


@router.get("/services")
def list_services(q:str="",category:str|None=None,active:bool|None=None,db:Session=Depends(get_db),_:User=Depends(current_user)):
    stmt=select(Service).where(Service.deleted_at.is_(None))
    if q:stmt=stmt.where(Service.name.ilike(f"%{q}%")|Service.code.ilike(f"%{q}%"))
    if category:stmt=stmt.where(Service.category==category)
    if active is not None:stmt=stmt.where(Service.active==active)
    rows=db.scalars(stmt.order_by(Service.name)).all();return [{"id":x.id,"code":x.code,"name":x.name,"category":x.category,"price":x.price,"estimated_minutes":x.estimated_minutes,"sla_hours":x.sla_hours,"active":x.active} for x in rows]


@router.post("/services",status_code=201)
def create_service(data:ServiceIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=Service(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="service",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.put("/services/{service_id}")
def update_service(service_id:int,data:ServiceIn,db:Session=Depends(get_db),user:User=Depends(manage_records)):
    x=db.get(Service,service_id)
    if not x or x.deleted_at:raise HTTPException(404,"Serviço não encontrado")
    for k,v in data.model_dump().items():setattr(x,k,v)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="service",entity_id=str(x.id)));db.commit();return {"message":"Serviço atualizado"}


@router.get("/payments")
def list_payments(status:str|None=None,method:str|None=None,order_id:int|None=None,db:Session=Depends(get_db),_:User=Depends(current_user)):
    stmt=select(Payment)
    if status:stmt=stmt.where(Payment.status==status)
    if method:stmt=stmt.where(Payment.method==method)
    if order_id:stmt=stmt.where(Payment.order_id==order_id)
    rows=db.scalars(stmt.order_by(Payment.created_at.desc())).all();return [{"id":x.id,"order_id":x.order_id,"order":db.get(Order,x.order_id).number,"amount":x.amount,"status":x.status,"method":x.method,"due_date":x.due_date} for x in rows]


@router.post("/payments",status_code=201)
def create_payment(data:PaymentIn,db:Session=Depends(get_db),user:User=Depends(manage_finance)):
    if not db.get(Order,data.order_id):raise HTTPException(404,"Ordem não encontrada")
    x=Payment(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="PAYMENT",entity="payment",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.patch("/payments/{payment_id}")
def update_payment(payment_id:int,status:str,db:Session=Depends(get_db),user:User=Depends(current_user)):
    x=db.get(Payment,payment_id)
    if not x:raise HTTPException(404,"Pagamento não encontrado")
    x.status=status;db.add(AuditLog(user_id=user.id,action="PAYMENT",entity="payment",entity_id=str(x.id),details={"status":status}));db.commit();return {"message":"Pagamento atualizado"}


@router.get("/users")
def list_users(q:str="",role:str|None=None,is_active:bool|None=None,db:Session=Depends(get_db),_:User=Depends(admin_only)):
    stmt=select(User)
    if q:stmt=stmt.where(User.name.ilike(f"%{q}%")|User.email.ilike(f"%{q}%"))
    if role:stmt=stmt.where(User.role==role)
    if is_active is not None:stmt=stmt.where(User.is_active==is_active)
    return [{"id":x.id,"name":x.name,"email":x.email,"role":x.role.value,"is_active":x.is_active,"last_login":x.last_login} for x in db.scalars(stmt.order_by(User.name)).all()]


@router.post("/users",status_code=201)
def create_user(data:UserIn,db:Session=Depends(get_db),user:User=Depends(admin_only)):
    if db.scalar(select(User).where(User.email==data.email.lower())):raise HTTPException(409,"E-mail já cadastrado")
    values=data.model_dump(exclude={"password","email"});x=User(**values,email=data.email.lower(),password_hash=hash_password(data.password));db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="user",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/appointments")
def list_appointments(db:Session=Depends(get_db),_:User=Depends(current_user)):
    rows=db.scalars(select(Appointment).order_by(Appointment.starts_at)).all();return [{"id":x.id,"title":x.title,"description":x.description,"starts_at":x.starts_at,"ends_at":x.ends_at,"kind":x.kind,"order_id":x.order_id} for x in rows]


@router.post("/appointments",status_code=201)
def create_appointment(data:AppointmentIn,db:Session=Depends(get_db),_:User=Depends(current_user)):
    if data.ends_at<=data.starts_at:raise HTTPException(400,"O término deve ser posterior ao início")
    obj=Appointment(**data.model_dump());db.add(obj);db.commit();return {"id":obj.id}


@router.get("/notifications")
def notifications(db:Session=Depends(get_db),user:User=Depends(current_user)):
    rows=db.scalars(select(Notification).where(Notification.user_id==user.id).order_by(Notification.created_at.desc())).all();return [{"id":x.id,"title":x.title,"message":x.message,"read":x.read,"created_at":x.created_at,"link":x.link} for x in rows]


@router.patch("/notifications/{notification_id}/read")
def read_notification(notification_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=db.get(Notification,notification_id)
    if not obj or obj.user_id!=user.id:raise HTTPException(404,"Notificação não encontrada")
    obj.read=True;db.commit();return {"message":"Notificação lida"}


@router.get("/audit")
def audit(db:Session=Depends(get_db),_:User=Depends(admin_only)):
    rows=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all();return [{"id":x.id,"action":x.action,"entity":x.entity,"entity_id":x.entity_id,"details":x.details,"created_at":x.created_at} for x in rows]


@router.get("/settings")
def get_settings(db:Session=Depends(get_db),_:User=Depends(current_user)):
    x=db.scalar(select(CompanySettings));return {"company_name":x.company_name,"segment":x.segment,"primary_color":x.primary_color,"terms":x.terms,"modules":x.modules}


@router.put("/settings")
def put_settings(data:SettingsIn,db:Session=Depends(get_db),user:User=Depends(admin_only)):
    x=db.scalar(select(CompanySettings));old={"company_name":x.company_name,"segment":x.segment};
    for key,value in data.model_dump().items():setattr(x,key,value)
    db.add(AuditLog(user_id=user.id,action="UPDATE",entity="settings",entity_id=str(x.id),details={"before":old}));db.commit();return {"message":"Configurações salvas"}


@router.get("/search")
def global_search(q:str,db:Session=Depends(get_db),_:User=Depends(current_user)):
    if len(q.strip())<2:return []
    pattern=f"%{q.strip()}%";orders=db.scalars(select(Order).where(Order.number.ilike(pattern)|Order.title.ilike(pattern)).limit(8)).all();customers=db.scalars(select(Customer).where(Customer.name.ilike(pattern)|Customer.document.ilike(pattern)|Customer.phone.ilike(pattern)).limit(8)).all();return [{"type":"order","id":x.id,"title":x.number,"subtitle":x.title} for x in orders]+[{"type":"customer","id":x.id,"title":x.name,"subtitle":x.phone or x.document} for x in customers]


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return {"services": [{"id": x.id, "name": x.name, "price": x.price} for x in db.scalars(select(Service).where(Service.deleted_at.is_(None))).all()], "employees": [{"id": x.id, "name": x.name} for x in db.scalars(select(Employee).where(Employee.deleted_at.is_(None))).all()], "departments": [{"id": x.id, "name": x.name} for x in db.scalars(select(Department)).all()]}


@router.post("/departments",status_code=201)
def create_department(name:str,db:Session=Depends(get_db),user:User=Depends(admin_only)):
    x=Department(name=name);db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="department",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/meta")
def meta(_:User=Depends(current_user)):
    return {"order_statuses":[x.value for x in OrderStatus],"priorities":["Baixa","Normal","Alta","Urgente","Crítica"],"roles":[x.value for x in Role],"payment_statuses":["Pendente","Pago","Parcial","Atrasado","Cancelado"],"payment_methods":["PIX","Dinheiro","Cartão de crédito","Cartão de débito","Boleto","Transferência"]}


@router.get("/reports")
def reports(db:Session=Depends(get_db),_:User=Depends(current_user)):
    by_status=db.execute(select(Order.status,func.count(Order.id)).where(Order.deleted_at.is_(None)).group_by(Order.status)).all()
    by_service=db.execute(select(Service.name,func.count(Order.id)).join(Order,Order.service_id==Service.id).where(Order.deleted_at.is_(None)).group_by(Service.name).order_by(func.count(Order.id).desc()).limit(10)).all()
    by_employee=db.execute(select(Employee.name,func.count(Order.id)).join(Order,Order.assignee_id==Employee.id).where(Order.deleted_at.is_(None)).group_by(Employee.name).order_by(func.count(Order.id).desc())).all()
    return {"by_status":[{"label":s.value,"value":c} for s,c in by_status],"by_service":[{"label":n,"value":c} for n,c in by_service],"by_employee":[{"label":n,"value":c} for n,c in by_employee]}


@router.get("/reports/orders.csv")
def export_orders(db:Session=Depends(get_db),user:User=Depends(current_user)):
    output=io.StringIO();writer=csv.writer(output,delimiter=";");writer.writerow(["Número","Cliente","Serviço","Prioridade","Status","Previsão"])
    for x in db.scalars(select(Order).where(Order.deleted_at.is_(None)).order_by(Order.created_at.desc())).all():writer.writerow([x.number,x.customer.name,x.service.name,x.priority,x.status.value,x.due_date or ""])
    db.add(AuditLog(user_id=user.id,action="EXPORT",entity="orders"));db.commit();return StreamingResponse(iter([output.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=ordens.csv"})


@router.post("/orders/{order_id}/attachments",status_code=201)
async def upload_attachment(order_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not db.get(Order,order_id):raise HTTPException(404,"Ordem não encontrada")
    allowed={"application/pdf","image/jpeg","image/png","application/vnd.openxmlformats-officedocument.wordprocessingml.document","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if file.content_type not in allowed:raise HTTPException(400,"Tipo de arquivo não permitido")
    content=await file.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise HTTPException(400,"Arquivo excede 10 MB")
    suffix=Path(file.filename or "arquivo").suffix;stored=f"{secrets.token_hex(16)}{suffix}";target=Path("uploads")/stored;target.write_bytes(content)
    obj=OrderAttachment(order_id=order_id,user_id=user.id,original_name=file.filename or "arquivo",stored_name=stored,content_type=file.content_type or "application/octet-stream",size=len(content));db.add(obj);db.add(OrderHistory(order_id=order_id,user_id=user.id,action="Anexo adicionado",new_value=obj.original_name));db.commit();return {"id":obj.id,"name":obj.original_name,"url":f"/uploads/{stored}"}


@router.get("/plans")
def list_plans(db:Session=Depends(get_db),_:User=Depends(current_user)):
    return [{"id":x.id,"name":x.name,"amount":x.amount,"periodicity":x.periodicity,"active":x.active} for x in db.scalars(select(Plan).order_by(Plan.name)).all()]


@router.post("/plans",status_code=201)
def create_plan(data:PlanIn,db:Session=Depends(get_db),user:User=Depends(admin_only)):
    x=Plan(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="plan",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/custom-fields")
def list_custom_fields(entity:str|None=None,db:Session=Depends(get_db),_:User=Depends(current_user)):
    stmt=select(CustomField).where(CustomField.active.is_(True));stmt=stmt.where(CustomField.entity==entity) if entity else stmt;return [{"id":x.id,"entity":x.entity,"label":x.label,"field_type":x.field_type,"required":x.required,"options":x.options} for x in db.scalars(stmt).all()]


@router.post("/custom-fields",status_code=201)
def create_custom_field(data:CustomFieldIn,db:Session=Depends(get_db),user:User=Depends(admin_only)):
    x=CustomField(**data.model_dump());db.add(x);db.flush();db.add(AuditLog(user_id=user.id,action="CREATE",entity="custom_field",entity_id=str(x.id)));db.commit();return {"id":x.id}


@router.get("/admin/demo-data")
def demo_summary(db: Session = Depends(get_db), _: User = Depends(admin_only)):
    models = [User, Customer, Department, Employee, Service, Order, OrderHistory, OrderComment, ChecklistItem, OrderTask, OrderAttachment, Appointment, Notification, Payment, Plan]
    counts = {m.__tablename__: db.scalar(select(func.count(m.id)).where(m.is_demo.is_(True))) or 0 for m in models}
    return {"has_demo_data": any(counts.values()), "counts": counts, "total": sum(counts.values())}


@router.delete("/admin/demo-data")
def delete_demo_data(data: DemoDelete, db: Session = Depends(get_db), user: User = Depends(admin_only)):
    if data.confirmation != "EXCLUIR DADOS DEMO":
        raise HTTPException(400, "Frase de confirmação incorreta")
    # Dependentes primeiro; tudo acontece em uma única transação.
    counts = {}
    for model in [Payment, OrderAttachment, OrderComment, ChecklistItem, OrderTask, Appointment, Notification, OrderHistory, Order, Employee, Service, Customer, Department, Plan]:
        items = db.scalars(select(model).where(model.is_demo.is_(True))).all()
        counts[model.__tablename__] = len(items)
        for item in items: db.delete(item)
    demo_users = db.scalars(select(User).where(User.is_demo.is_(True), User.id != user.id)).all()
    counts["users"] = len(demo_users)
    for item in demo_users: db.delete(item)
    # O administrador demo atual é preservado para não invalidar a operação; perde a marca demo.
    if user.is_demo: user.is_demo = False
    db.add(AuditLog(user_id=user.id, action="DELETE_DEMO_DATA", entity="system", details=counts))
    db.commit()
    return {"message": "Dados demonstrativos removidos permanentemente", "deleted": counts}

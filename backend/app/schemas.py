from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from .models import OrderStatus, Role


class Login(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict
    refresh_token: str | None = None


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8,max_length=72)


class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    document: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    city: str | None = None
    state: str | None = Field(default=None, max_length=2)
    status: str = "Ativo"
    notes: str | None = None


class CustomerOut(CustomerIn):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderIn(BaseModel):
    customer_id: int
    service_id: int
    assignee_id: int | None = None
    department_id: int | None = None
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=3)
    priority: str = "Normal"
    status: OrderStatus = OrderStatus.OPEN
    due_date: date | None = None
    value: float | None = Field(default=None, ge=0)


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    internal: bool = True


class ChecklistIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    assignee_id: int | None = None
    status: str = "Pendente"
    priority: str = "Normal"
    due_date: date | None = None


class AppointmentIn(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    order_id: int | None = None
    employee_id: int | None = None
    kind: str = "Compromisso"


class SettingsIn(BaseModel):
    company_name: str
    segment: str
    primary_color: str = "#6D5DFB"
    terms: dict[str, str]
    modules: dict[str, bool]


class EmployeeIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    job_title: str = Field(min_length=2, max_length=100)
    department_id: int | None = None
    email: EmailStr | None = None
    phone: str | None = None
    status: str = "Ativo"


class ServiceIn(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=150)
    category: str = Field(min_length=2, max_length=100)
    price: float | None = Field(default=None, ge=0)
    estimated_minutes: int = Field(default=60, gt=0)
    sla_hours: int = Field(default=24, gt=0)
    active: bool = True


class PaymentIn(BaseModel):
    order_id: int
    amount: float = Field(gt=0)
    status: str = "Pendente"
    method: str = "PIX"
    due_date: date | None = None


class UserIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: Role = Role.VIEWER
    is_active: bool = True


class PlanIn(BaseModel):
    name:str=Field(min_length=2,max_length=150)
    amount:float=Field(ge=0)
    periodicity:str
    active:bool=True


class CustomFieldIn(BaseModel):
    entity:str
    label:str=Field(min_length=2,max_length=100)
    field_type:str
    required:bool=False
    options:list[str]|None=None


class DemoDelete(BaseModel):
    confirmation: str

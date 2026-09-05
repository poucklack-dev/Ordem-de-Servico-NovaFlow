from datetime import date, timedelta
from random import choice
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import ChecklistItem, Customer, Department, Employee, Notification, Order, OrderComment, OrderHistory, OrderStatus, Payment, Role, Service, User, CompanySettings
from .security import hash_password


def seed_demo(db: Session) -> None:
    existing_admin=db.scalar(select(User).where(User.email == "admin@demo.com"))
    if existing_admin:
        demo_orders=db.scalars(select(Order).where(Order.is_demo.is_(True))).all()
        for order in demo_orders:
            if not db.scalar(select(OrderComment).where(OrderComment.order_id==order.id)):
                db.add(OrderComment(order_id=order.id,user_id=existing_admin.id,content="Atendimento registrado e aguardando acompanhamento da equipe.",internal=True,is_demo=True))
            if not db.scalar(select(ChecklistItem).where(ChecklistItem.order_id==order.id)):
                for position,title in enumerate(["Validar solicitação","Executar atendimento","Confirmar conclusão"]):db.add(ChecklistItem(order_id=order.id,title=title,completed=position==0,position=position,is_demo=True))
        if not db.scalar(select(Notification).where(Notification.user_id==existing_admin.id)):
            db.add(Notification(user_id=existing_admin.id,title="Bem-vindo ao NovaFlow",message="A operação demonstrativa está pronta para explorar.",is_demo=True))
        db.commit()
        return
    users = [
        User(name="Administrador Demo", email="admin@demo.com", password_hash=hash_password("Admin@123"), role=Role.ADMIN, is_demo=True),
        User(name="Gerente Demo", email="gerente@demo.com", password_hash=hash_password("Demo@123"), role=Role.MANAGER, is_demo=True),
        User(name="Atendente Demo", email="atendente@demo.com", password_hash=hash_password("Demo@123"), role=Role.ATTENDANT, is_demo=True),
        User(name="Técnico Demo", email="tecnico@demo.com", password_hash=hash_password("Demo@123"), role=Role.OPERATOR, is_demo=True),
    ]
    departments = [Department(name=n, is_demo=True) for n in ["Atendimento", "Operações", "Financeiro", "Administrativo"]]
    db.add_all(users + departments)
    db.flush()
    customers = [Customer(name=n, document=f"000000000{i:02d}", phone=f"(71) 99999-{1000+i}", email=f"cliente{i}@exemplo.com", city="Salvador", state="BA", is_demo=True) for i, n in enumerate([
        "Ana Martins", "Bruno Almeida", "Carla Souza", "Daniel Rocha", "Elisa Santos", "Felipe Lima", "Gabriela Alves", "Henrique Melo", "Isabela Costa", "João Ribeiro", "Larissa Gomes", "Marcos Nunes", "Natália Freitas", "Otávio Barros", "Patrícia Correia"
    ], 1)]
    db.add_all(customers)
    employees = [Employee(name=n, job_title=c, department_id=departments[i % len(departments)].id, email=f"colaborador{i+1}@demo.com", is_demo=True) for i, (n, c) in enumerate([
        ("Carlos Mendes", "Técnico Sênior"), ("Beatriz Luz", "Analista"), ("Rafael Dias", "Técnico"), ("Camila Reis", "Atendente"), ("Lucas Moraes", "Supervisor"), ("Aline Castro", "Financeiro")
    ])]
    services = [Service(code=f"SRV-{i:03d}", name=n, category=c, price=p, estimated_minutes=60, sla_hours=24, is_demo=True) for i, (n,c,p) in enumerate([
        ("Diagnóstico técnico","Suporte",120), ("Manutenção preventiva","Manutenção",250), ("Instalação","Operações",180), ("Suporte remoto","Suporte",90), ("Visita técnica","Atendimento",150), ("Reparo elétrico","Manutenção",320), ("Configuração","Tecnologia",140), ("Inspeção","Qualidade",110), ("Consultoria","Administrativo",400), ("Treinamento","Capacitação",300)
    ], 1)]
    db.add_all(employees + services)
    db.flush()
    statuses = list(OrderStatus)
    for i in range(1, 26):
        order = Order(number=f"OS-{2026000+i}", customer_id=customers[(i-1)%15].id, service_id=services[(i-1)%10].id, assignee_id=employees[(i-1)%6].id, department_id=departments[(i-1)%4].id, title=f"{services[(i-1)%10].name} solicitado", description="Atendimento demonstrativo com informações realistas para apresentação do sistema.", priority=choice(["Baixa","Normal","Alta","Urgente"]), status=statuses[i % len(statuses)], due_date=date.today()+timedelta(days=(i%9)-3), value=services[(i-1)%10].price, is_demo=True)
        db.add(order); db.flush()
        db.add(OrderHistory(order_id=order.id, user_id=users[0].id, action="Ordem criada", new_value=order.status.value, is_demo=True))
        db.add(Payment(order_id=order.id, amount=order.value, status=choice(["Pendente","Pago","Atrasado"]), method=choice(["PIX","Cartão de crédito","Boleto"]), due_date=order.due_date, is_demo=True))
        db.add(OrderComment(order_id=order.id,user_id=users[0].id,content="Atendimento registrado e aguardando acompanhamento da equipe.",internal=True,is_demo=True))
        for position,title in enumerate(["Validar solicitação","Executar atendimento","Confirmar conclusão"]):
            db.add(ChecklistItem(order_id=order.id,title=title,completed=position==0,position=position,is_demo=True))
    db.add(Notification(user_id=users[0].id,title="Bem-vindo ao NovaFlow",message="Os dados demonstrativos estão prontos para explorar.",is_demo=True))
    if not db.scalar(select(CompanySettings)):
        db.add(CompanySettings(company_name="NovaGestão Serviços", segment="Serviços"))
    db.commit()

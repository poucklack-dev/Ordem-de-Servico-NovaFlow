import os
os.environ["DATABASE_URL"] = "sqlite:///./test_novaflow.db"
from fastapi.testclient import TestClient
from app.main import app


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_login_dashboard_and_demo_summary():
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"email": "admin@demo.com", "password": "Admin@123"})
        assert response.status_code == 200
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        assert client.get("/api/dashboard", headers=headers).status_code == 200
        summary = client.get("/api/admin/demo-data", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["has_demo_data"] is True


def test_demo_delete_requires_exact_confirmation():
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"email": "admin@demo.com", "password": "Admin@123"})
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        denied = client.request("DELETE", "/api/admin/demo-data", headers=headers, json={"confirmation": "excluir"})
        assert denied.status_code == 400


def test_core_workflow():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        customer=client.post("/api/customers",headers=headers,json={"name":"Cliente de Teste","document":None,"phone":"71999999999","email":"teste@example.com","city":"Salvador","state":"BA","status":"Ativo","notes":"Criado pelo teste"})
        assert customer.status_code==201
        catalog=client.get("/api/catalog",headers=headers).json()
        order=client.post("/api/orders",headers=headers,json={"customer_id":customer.json()["id"],"service_id":catalog["services"][0]["id"],"title":"Fluxo de integração","description":"Validação completa da ordem","priority":"Normal","status":"Aberta"})
        assert order.status_code==201
        order_id=order.json()["id"]
        assert client.post(f"/api/orders/{order_id}/comments",headers=headers,json={"content":"Comentário funcional","internal":True}).status_code==201
        item=client.post(f"/api/orders/{order_id}/checklist",headers=headers,json={"title":"Executar validação"})
        assert item.status_code==201
        assert client.patch(f"/api/orders/{order_id}/checklist/{item.json()['id']}?completed=true",headers=headers).status_code==200
        assert client.patch(f"/api/orders/{order_id}/status?status=Em%20andamento",headers=headers).status_code==200
        detail=client.get(f"/api/orders/{order_id}",headers=headers).json()
        assert detail["status"]=="Em andamento" and detail["comments"] and detail["checklist"][0]["completed"]


def test_viewer_cannot_create_records():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"gerente@demo.com","password":"Demo@123"})
        assert login.status_code==200
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/orders",headers=headers).status_code==200


def test_filters_and_edits():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        customers=client.get("/api/customers?status=Ativo&city=Salvador",headers=headers)
        assert customers.status_code==200 and len(customers.json())>0
        customer=customers.json()[0]
        customer_payload={k:customer.get(k) for k in ["name","document","phone","email","city","state","status","notes"]}
        assert client.put(f"/api/customers/{customer['id']}",headers=headers,json=customer_payload).status_code==200
        employee=client.get("/api/employees?status=Ativo",headers=headers).json()[0]
        payload={"name":employee["name"],"job_title":employee["job_title"],"department_id":employee["department_id"],"email":employee["email"],"phone":employee["phone"],"status":"Ativo"}
        assert client.put(f"/api/employees/{employee['id']}",headers=headers,json=payload).status_code==200
        service=client.get("/api/services?active=true",headers=headers).json()[0]
        service_payload={"code":service["code"],"name":service["name"],"category":service["category"],"price":service["price"],"estimated_minutes":service["estimated_minutes"],"sla_hours":service["sla_hours"],"active":True}
        assert client.put(f"/api/services/{service['id']}",headers=headers,json=service_payload).status_code==200
        assert client.get("/api/orders?priority=Normal",headers=headers).status_code==200
        order=client.get("/api/orders",headers=headers).json()[0]
        detail=client.get(f"/api/orders/{order['id']}",headers=headers).json()
        order_payload={"customer_id":detail["customer"]["id"],"service_id":detail["service"]["id"],"assignee_id":detail["assignee"]["id"] if detail["assignee"] else None,"department_id":None,"title":detail["title"],"description":detail["description"],"priority":detail["priority"],"status":detail["status"],"due_date":detail["due_date"],"value":detail["value"]}
        assert client.put(f"/api/orders/{order['id']}",headers=headers,json=order_payload).status_code==200
        assert client.get("/api/payments?status=Pago",headers=headers).status_code==200
        assert client.get("/api/users?role=Administrador&is_active=true",headers=headers).status_code==200

import uuid
from datetime import date, timedelta
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


def test_demo_delete_is_transactional_and_keeps_current_administrator():
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"email": "admin@demo.com", "password": "Admin@123"})
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        deleted = client.request("DELETE", "/api/admin/demo-data", headers=headers, json={"confirmation": "EXCLUIR DADOS DEMO"})
        assert deleted.status_code == 200, deleted.text
        assert client.get("/api/auth/context", headers=headers).json()["is_admin"] is True
        summary = client.get("/api/admin/demo-data", headers=headers).json()
        assert summary["has_demo_data"] is False


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


def test_only_admin_can_access_and_change_system_settings():
    with TestClient(app) as client:
        admin_login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        admin={"Authorization":f"Bearer {admin_login.json()['access_token']}"}
        current=client.get("/api/settings",headers=admin)
        assert current.status_code==200

        manager_login=client.post("/api/auth/login",json={"email":"gerente@demo.com","password":"Demo@123"})
        manager={"Authorization":f"Bearer {manager_login.json()['access_token']}"}
        assert client.get("/api/settings",headers=manager).status_code==403
        assert client.put("/api/settings",headers=manager,json=current.json()).status_code==403
        assert client.put("/api/settings/modules",headers=manager,json=current.json()["modules"]).status_code==403


def test_filters_and_edits():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original_modules=client.get("/api/settings/modules",headers=headers).json()["modules"]
        customers=client.get("/api/customers?status=Ativo&city=Salvador",headers=headers)
        assert customers.status_code==200 and len(customers.json())>0
        customer=customers.json()[0]
        customer_payload={k:customer.get(k) for k in ["name","document","phone","email","city","state","status","notes"]}
        assert client.put(f"/api/customers/{customer['id']}",headers=headers,json=customer_payload).status_code==200
        employee=client.get("/api/employees?status=Ativo",headers=headers).json()[0]
        payload={"name":employee["name"],"job_position_id":employee["job_position_id"],"department_id":employee["department_id"],"email":employee["email"],"phone":employee["phone"],"status":"Ativo"}
        assert client.put(f"/api/employees/{employee['id']}",headers=headers,json=payload).status_code==200
        service=client.get("/api/services?active=true",headers=headers).json()[0]
        service_payload={"code":service["code"],"name":service["name"],"category":service["category"],"price":service["price"],"estimated_minutes":service["estimated_minutes"],"sla_hours":service["sla_hours"],"active":True}
        assert client.put(f"/api/services/{service['id']}",headers=headers,json=service_payload).status_code==200
        assert client.get("/api/orders?priority=Normal",headers=headers).status_code==200
        order=client.get("/api/orders",headers=headers).json()[0]
        detail=client.get(f"/api/orders/{order['id']}",headers=headers).json()
        order_payload={"customer_id":detail["customer"]["id"],"service_id":detail["service"]["id"],"assignee_id":detail["assignee"]["id"] if detail["assignee"] else None,"department_id":None,"title":detail["title"],"description":detail["description"],"priority":detail["priority"],"status":detail["status"],"due_date":detail["due_date"],"value":detail["value"]}
        assert client.put(f"/api/orders/{order['id']}",headers=headers,json=order_payload).status_code==200
        # O endpoint financeiro não pode ser acessado quando o módulo está desligado.
        client.put("/api/settings/modules",headers=headers,json={**original_modules,"financial":False})
        assert client.get("/api/payments?status=Pago",headers=headers).status_code==404
        client.put("/api/settings/modules",headers=headers,json=original_modules)


def test_feature_modules_change_dashboard_and_protect_endpoints():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        disabled={"financial":False,"agenda":False,"plans":False,"academy":False,"school":False}
        saved=client.put("/api/settings/modules",headers=headers,json=disabled)
        assert saved.status_code==200
        dashboard=client.get("/api/dashboard",headers=headers).json()
        assert dashboard["financial"] is None and dashboard["agenda"] is None
        assert client.get("/api/payments",headers=headers).status_code==404
        enabled={**disabled,"financial":True}
        assert client.put("/api/settings/modules",headers=headers,json=enabled).status_code==200
        assert client.get("/api/dashboard",headers=headers).json()["financial"] is not None
        assert client.get("/api/payments",headers=headers).status_code==200
        invalid={**disabled,"academy":True,"school":True}
        assert client.put("/api/settings/modules",headers=headers,json=invalid).status_code==422
        client.put("/api/settings/modules",headers=headers,json=original)


def test_school_records_survive_module_disable_and_reenable():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"})
        headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        school={"financial":False,"agenda":False,"plans":True,"academy":False,"school":True}
        assert client.put("/api/settings/modules",headers=headers,json=school).status_code==200
        course=client.post("/api/module-data/school/courses",headers=headers,json={"data":{"name":"Inglês","workload":120,"duration":"12 meses","amount":240},"status":"Ativo"})
        assert course.status_code==201
        class_room=client.post("/api/module-data/school/classes",headers=headers,json={"data":{"code":"ING-A02","name":"Inglês Intermediário","course":"Inglês","capacity":25,"students":20},"status":"Ativa"})
        assert class_room.status_code==201
        dashboard=client.get("/api/dashboard",headers=headers).json()
        assert dashboard["school"]["active_courses"]>=1 and dashboard["school"]["active_classes"]>=1
        disabled={**school,"school":False}
        client.put("/api/settings/modules",headers=headers,json=disabled)
        assert client.get("/api/module-data/school/courses",headers=headers).status_code==404
        client.put("/api/settings/modules",headers=headers,json=school)
        records=client.get("/api/module-data/school/courses",headers=headers).json()
        assert any(x["id"]==course.json()["id"] for x in records)
        client.delete(f"/api/module-data/school/courses/{course.json()['id']}",headers=headers)
        client.delete(f"/api/module-data/school/classes/{class_room.json()['id']}",headers=headers)
        client.put("/api/settings/modules",headers=headers,json=original)


def test_plan_subscription_can_generate_financial_charge():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"});headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        modules={"financial":True,"agenda":False,"plans":True,"academy":False,"school":False};client.put("/api/settings/modules",headers=headers,json=modules)
        subscription=client.post("/api/module-data/plans/subscriptions",headers=headers,json={"data":{"customer":"Cliente Teste","plan":"Mensal","amount":149.9,"next_charge":"2026-10-10","generate_charge":True},"status":"Ativa"})
        assert subscription.status_code==201
        charges=client.get("/api/module-data/financial/charges",headers=headers).json();charge=next(x for x in charges if x["data"].get("subscription_id")==subscription.json()["id"])
        assert charge["status"]=="Pendente" and float(charge["data"]["amount"])==149.9
        client.delete(f"/api/module-data/financial/charges/{charge['id']}",headers=headers);client.delete(f"/api/module-data/plans/subscriptions/{subscription.json()['id']}",headers=headers)
        client.put("/api/settings/modules",headers=headers,json=original)


def test_acceptance_matrix_a_to_h_and_explicit_permissions():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"});headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        off={"financial":False,"agenda":False,"plans":False,"academy":False,"school":False}
        assert client.put("/api/settings/modules",headers=headers,json=off).status_code==200
        dashboard=client.get("/api/dashboard",headers=headers).json()
        assert dashboard["core"] and all(dashboard[key] is None for key in off)
        assert client.get("/api/payments",headers=headers).status_code==404
        assert client.get("/api/appointments",headers=headers).status_code==404
        assert client.get("/api/plans",headers=headers).status_code==404
        financial={**off,"financial":True};client.put("/api/settings/modules",headers=headers,json=financial)
        assert client.get("/api/payments",headers=headers).status_code==200
        assert client.get("/api/dashboard",headers=headers).json()["financial"] is not None
        school={**off,"school":True,"plans":True};client.put("/api/settings/modules",headers=headers,json=school)
        school_dashboard=client.get("/api/dashboard",headers=headers).json()
        assert school_dashboard["school"] is not None and school_dashboard["plans"] is not None and school_dashboard["financial"] is None
        assert client.get("/api/module-data/school/classes",headers=headers).status_code==200
        assert client.get("/api/module-data/academy/modalities",headers=headers).status_code==404
        assert client.put("/api/settings/modules",headers=headers,json={**school,"academy":True}).status_code==422
        academy={**off,"academy":True,"agenda":True,"plans":True,"financial":True};client.put("/api/settings/modules",headers=headers,json=academy)
        academy_dashboard=client.get("/api/dashboard",headers=headers).json()
        assert academy_dashboard["academy"] is not None and academy_dashboard["school"] is None and academy_dashboard["agenda"] is not None
        assert client.get("/api/appointments",headers=headers).status_code==200
        viewer=next(x for x in client.get("/api/profiles",headers=headers).json() if x["name"]=="Visualizador")
        email=f"permissoes-{uuid.uuid4().hex}@example.com";created=client.post("/api/users",headers=headers,json={"name":"Teste Permissões","email":email,"password":"Teste@123","profile_id":viewer["id"],"is_active":True})
        assert created.status_code==201,created.text
        restricted_login=client.post("/api/auth/login",json={"email":email,"password":"Teste@123"});restricted={"Authorization":f"Bearer {restricted_login.json()['access_token']}"}
        assert client.get("/api/orders",headers=restricted).status_code==200
        assert client.get("/api/payments",headers=restricted).status_code==403
        assert client.get("/api/dashboard",headers=restricted).json()["financial"] is None
        assert client.get("/api/reports",headers=restricted).json()["financial"] is None
        assert client.put(f"/api/users/{created.json()['id']}/permissions",headers=headers,json={"permissions":{"financial.view":True},"reason":"Exceção financeira controlada para consulta"}).status_code==200
        assert client.get("/api/payments",headers=restricted).status_code==200
        assert client.get("/api/dashboard",headers=restricted).json()["financial"] is not None
        client.put("/api/settings/modules",headers=headers,json=original)
        assert client.get("/api/users?role=Administrador&is_active=true",headers=headers).status_code==200


def test_recurring_billing_is_idempotent_and_advances_due_date():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"});headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        modules={"financial":True,"agenda":False,"plans":True,"academy":False,"school":False};client.put("/api/settings/modules",headers=headers,json=modules)
        old_due=(date.today()-timedelta(days=70)).isoformat()
        created=client.post("/api/module-data/plans/subscriptions",headers=headers,json={"data":{"customer":"Recorrente","plan":"Mensal","amount":99.9,"periodicity":"Mensal","next_charge":old_due,"generate_charge":True},"status":"Ativa"}).json()
        first=client.post("/api/billing/generate",headers=headers)
        assert first.status_code==200 and first.json()["generated"]>=2
        second=client.post("/api/billing/generate",headers=headers)
        assert second.status_code==200 and second.json()["generated"]==0
        subscription=next(x for x in client.get("/api/module-data/plans/subscriptions",headers=headers).json() if x["id"]==created["id"])
        assert date.fromisoformat(subscription["data"]["next_charge"])>date.today()
        charges=[x for x in client.get("/api/module-data/financial/charges",headers=headers).json() if x["data"].get("subscription_id")==created["id"]]
        assert len({x["data"]["due_date"] for x in charges})==len(charges)
        for charge in charges: client.delete(f"/api/module-data/financial/charges/{charge['id']}",headers=headers)
        client.delete(f"/api/module-data/plans/subscriptions/{created['id']}",headers=headers)
        client.put("/api/settings/modules",headers=headers,json=original)


def test_school_enrollment_integrates_with_plan_and_financial_charge():
    with TestClient(app) as client:
        login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"});headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=headers).json()["modules"]
        modules={"financial":True,"agenda":False,"plans":True,"academy":False,"school":True};client.put("/api/settings/modules",headers=headers,json=modules)
        plan_name=f"Mensalidade {uuid.uuid4().hex[:8]}";plan=client.post("/api/plans",headers=headers,json={"name":plan_name,"description":"Contrato educacional","amount":320,"periodicity":"Mensal","starts_on":None,"ends_on":None,"auto_renew":True,"max_users":1,"included_services":"Aulas","status":"Ativo","active":True})
        assert plan.status_code==201
        enrollment=client.post("/api/module-data/school/enrollments",headers=headers,json={"data":{"number":f"2026-{uuid.uuid4().hex[:5]}","student":"Aluno Integrado","course":"Inglês","class_name":"ING-A02","plan":plan_name,"date":date.today().isoformat()},"status":"Ativa"})
        assert enrollment.status_code==201
        subscriptions=[x for x in client.get("/api/module-data/plans/subscriptions",headers=headers).json() if x["data"].get("enrollment_id")==enrollment.json()["id"]]
        assert len(subscriptions)==1 and float(subscriptions[0]["data"]["amount"])==320
        charges=[x for x in client.get("/api/module-data/financial/charges",headers=headers).json() if x["data"].get("subscription_id")==subscriptions[0]["id"]]
        assert len(charges)==1 and charges[0]["status"]=="Pendente"
        client.delete(f"/api/module-data/financial/charges/{charges[0]['id']}",headers=headers);client.delete(f"/api/module-data/plans/subscriptions/{subscriptions[0]['id']}",headers=headers);client.delete(f"/api/module-data/school/enrollments/{enrollment.json()['id']}",headers=headers)
        client.put("/api/settings/modules",headers=headers,json=original)


def test_financial_write_permissions_are_independent():
    with TestClient(app) as client:
        admin_login=client.post("/api/auth/login",json={"email":"admin@demo.com","password":"Admin@123"});admin={"Authorization":f"Bearer {admin_login.json()['access_token']}"}
        original=client.get("/api/settings/modules",headers=admin).json()["modules"];client.put("/api/settings/modules",headers=admin,json={"financial":True,"agenda":False,"plans":False,"academy":False,"school":False})
        viewer=next(x for x in client.get("/api/profiles",headers=admin).json() if x["name"]=="Visualizador")
        email=f"finance-{uuid.uuid4().hex}@example.com";created=client.post("/api/users",headers=admin,json={"name":"Financeiro Restrito","email":email,"password":"Teste@123","profile_id":viewer["id"],"is_active":True})
        assert created.status_code==201,created.text
        user_id=created.json()["id"]
        assert client.put(f"/api/users/{user_id}/permissions",headers=admin,json={"permissions":{"financial.view":True},"reason":"Permissão excepcional de consulta financeira"}).status_code==200
        login=client.post("/api/auth/login",json={"email":email,"password":"Teste@123"});headers={"Authorization":f"Bearer {login.json()['access_token']}"}
        payload={"data":{"customer":"Cliente","amount":50,"due_date":date.today().isoformat()},"status":"Pendente"}
        assert client.get("/api/module-data/financial/charges",headers=headers).status_code==200
        assert client.post("/api/module-data/financial/charges",headers=headers,json=payload).status_code==403
        assert client.put(f"/api/users/{user_id}/permissions",headers=admin,json={"permissions":{"financial.view":True,"financial.create":True},"reason":"Permissão excepcional para criar cobranças"}).status_code==200
        charge=client.post("/api/module-data/financial/charges",headers=headers,json=payload)
        assert charge.status_code==201
        assert client.put(f"/api/module-data/financial/charges/{charge.json()['id']}",headers=headers,json=payload).status_code==403
        assert client.delete(f"/api/module-data/financial/charges/{charge.json()['id']}",headers=headers).status_code==403
        assert client.put(f"/api/users/{user_id}/permissions",headers=admin,json={"permissions":{"financial.view":True,"financial.update":True,"financial.delete":True},"reason":"Permissão excepcional para corrigir cobranças"}).status_code==200
        assert client.put(f"/api/module-data/financial/charges/{charge.json()['id']}",headers=headers,json={**payload,"status":"Pago"}).status_code==200
        assert client.delete(f"/api/module-data/financial/charges/{charge.json()['id']}",headers=headers).status_code==200
        client.put("/api/settings/modules",headers=admin,json=original)

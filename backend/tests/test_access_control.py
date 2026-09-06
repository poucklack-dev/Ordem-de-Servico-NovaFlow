"""Acceptance and regression coverage for cargo -> profile -> permission -> scope."""

import json
import uuid

import pytest


PASSWORD = "Teste@123"
ALL_OFF = {"financial": False, "agenda": False, "plans": False, "academy": False, "school": False}


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def login(client, email="admin@demo.com", password="Admin@123"):
    body = checked(client.post("/api/auth/login", json={"email": email, "password": password}))
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def admin(client):
    return login(client)


def profile(client, admin, name):
    return next(row for row in checked(client.get("/api/profiles", headers=admin)) if row["name"] == name)


def job(client, admin, name):
    return next(row for row in checked(client.get("/api/job-positions", headers=admin)) if row["name"] == name)


def department(client, admin, label):
    return checked(client.post("/api/departments", headers=admin, params={"name": label}), 201)["id"]


def employee(client, admin, position_id, department_id, **extra):
    payload = {"name": f"Pessoa {uuid.uuid4().hex[:8]}", "job_position_id": position_id,
               "department_id": department_id, "status": "Ativo", **extra}
    created = checked(client.post("/api/employees", headers=admin, json=payload), 201)
    return {**payload, "id": created["id"]}


def account(client, admin, *, employee_id=None, profile_id=None, **extra):
    payload = {"name": "Conta de teste", "email": f"access-{uuid.uuid4().hex}@example.com",
               "password": PASSWORD, "is_active": True, **extra}
    if employee_id is not None:
        payload["employee_id"] = employee_id
    else:
        payload["profile_id"] = profile_id
        selected = next(row for row in checked(client.get("/api/profiles", headers=admin)) if row["id"] == profile_id)
        scope = payload.get("scope") or selected["default_scope"]
        if scope == "DEPARTMENT" and not payload.get("department_id"):
            payload["department_id"] = department(client, admin, f"Conta setor {uuid.uuid4().hex[:8]}")
        if scope == "MANAGED_DEPARTMENTS" and not payload.get("managed_department_ids"):
            payload["managed_department_ids"] = [department(client, admin, f"Conta gerência {uuid.uuid4().hex[:8]}")]
    created = checked(client.post("/api/users", headers=admin, json=payload), 201)
    return created, login(client, payload["email"], PASSWORD)


def context(client, headers):
    return checked(client.get("/api/auth/context", headers=headers))


def update_employee(client, admin, person, **changes):
    payload = {key: value for key, value in person.items() if key != "id"}
    payload.update(changes)
    return client.put(f"/api/employees/{person['id']}", headers=admin, json=payload)


def profile_payload(row, **changes):
    return {"name": row["name"], "description": row.get("description"),
            "default_scope": row["default_scope"], "active": row["active"],
            "permissions": row["permissions"], **changes}


def job_payload(row, **changes):
    return {"name": row["name"], "description": row.get("description"),
            "profile_id": row["profile_id"], "department_id": row.get("department_id"),
            "default_scope": row["default_scope"], "active": row["active"], **changes}


def custom_profile(client, admin, permissions, scope="DEPARTMENT"):
    return checked(client.post("/api/profiles", headers=admin, json={
        "name": f"Coordenador {uuid.uuid4().hex[:8]}", "description": "Perfil explícito de teste",
        "default_scope": scope, "active": True, "permissions": permissions,
    }), 201)


def custom_job(client, admin, associated_profile, scope="DEPARTMENT"):
    return checked(client.post("/api/job-positions", headers=admin, json={
        "name": f"Coordenação {uuid.uuid4().hex[:8]}", "profile_id": associated_profile["id"],
        "default_scope": scope, "active": True,
    }), 201)


def test_acceptance_1_employee_profile_is_derived_from_analyst_job(client, admin):
    position = job(client, admin, "Analista de Operações")
    sector = department(client, admin, "Setor de aceite")
    person = employee(client, admin, position["id"], sector)
    created, headers = account(client, admin, employee_id=person["id"])
    actual = context(client, headers)
    assert actual["profile"]["name"] == "Analista"
    assert actual["employee_id"] == person["id"]
    stored = next(row for row in checked(client.get("/api/users", headers=admin)) if row["id"] == created["id"])
    assert stored["profile_id"] is None
    assert stored["effective_profile"]["name"] == "Analista"


def test_acceptance_2_promotion_recalculates_profile_with_same_token(client, admin):
    analyst = job(client, admin, "Analista de Operações")
    supervisor = job(client, admin, "Supervisor Operacional")
    sector = department(client, admin, "Setor promoção")
    person = employee(client, admin, analyst["id"], sector)
    _, headers = account(client, admin, employee_id=person["id"])
    assert "orders.assign" not in context(client, headers)["permissions"]
    checked(update_employee(client, admin, person, job_position_id=supervisor["id"]))
    actual = context(client, headers)
    assert actual["profile"]["name"] == "Supervisor"
    assert "orders.assign" in actual["permissions"]


def test_acceptance_3_supervisor_cannot_access_permission_administration(client, admin):
    _, headers = account(client, admin, profile_id=profile(client, admin, "Supervisor")["id"])
    for path in ("/api/profiles", "/api/permissions", "/api/settings", "/api/audit"):
        assert client.get(path, headers=headers).status_code == 403, path


def order_fixture(client, admin, *, scope="DEPARTMENT", profile_name="Supervisor"):
    sectors = [department(client, admin, f"Acesso {uuid.uuid4().hex[:8]} {letter}") for letter in "ABC"]
    associated_profile = profile(client, admin, profile_name)
    position = custom_job(client, admin, associated_profile, scope)
    people = [employee(client, admin, position["id"], sector,
        **({"managed_department_ids": sectors[:2] if index == 0 else [sector]} if scope == "MANAGED_DEPARTMENTS" else {}))
        for index, sector in enumerate((sectors[0], sectors[0], sectors[1], sectors[2]))]
    _, headers = account(client, admin, employee_id=people[0]["id"])
    service_id = checked(client.get("/api/catalog", headers=admin))["services"][0]["id"]
    marker = f"Scope{uuid.uuid4().hex[:8]}"
    rows = []
    for index, person in enumerate(people):
        customer = checked(client.post("/api/customers", headers=admin, json={"name": f"{marker} cliente {index}", "department_id": person["department_id"]}), 201)
        payload = {"customer_id": customer["id"], "service_id": service_id,
                   "assignee_id": person["id"], "department_id": person["department_id"],
                   "title": f"{marker} ordem {index}", "description": "Validação de isolamento de dados",
                   "priority": "Normal", "status": "Aberta"}
        created = checked(client.post("/api/orders", headers=admin, json=payload), 201)
        rows.append({**payload, **created})
    return {"headers": headers, "rows": rows, "people": people, "sectors": sectors,
            "marker": marker, "job": position}


def test_acceptance_4_supervisor_can_access_own_department_orders(client, admin):
    world = order_fixture(client, admin)
    for row in world["rows"][:2]:
        checked(client.get(f"/api/orders/{row['id']}", headers=world["headers"]))
        checked(client.patch(f"/api/orders/{row['id']}/status", headers=world["headers"], params={"status": "Em andamento"}))


def test_acceptance_5_supervisor_cannot_access_other_department_orders(client, admin):
    world = order_fixture(client, admin)
    for row in world["rows"][2:]:
        assert client.get(f"/api/orders/{row['id']}", headers=world["headers"]).status_code == 404
        assert client.patch(f"/api/orders/{row['id']}/status", headers=world["headers"], params={"status": "Concluída"}).status_code == 404
        assert client.post(f"/api/orders/{row['id']}/comments", headers=world["headers"], json={"content": "Tentativa externa"}).status_code == 404
        assert checked(client.get(f"/api/orders/{row['id']}", headers=admin))["status"] == "Aberta"


def test_acceptance_6_analyst_cannot_manage_users(client, admin):
    _, headers = account(client, admin, profile_id=profile(client, admin, "Analista")["id"])
    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.post("/api/users", headers=headers, json={"name": "Escalada", "email": "escalada@example.com", "password": PASSWORD,
        "profile_id": profile(client, admin, "Administrador")["id"]}).status_code == 403


def test_acceptance_7_manager_cannot_activate_system_module(client, admin):
    _, headers = account(client, admin, profile_id=profile(client, admin, "Gerente")["id"])
    before = checked(client.get("/api/settings/modules", headers=admin))["modules"]
    assert client.put("/api/settings/modules", headers=headers, json={**ALL_OFF, "financial": True}).status_code == 403
    assert checked(client.get("/api/settings/modules", headers=admin))["modules"] == before


def test_acceptance_8_demotion_is_immediate_and_audited(client, admin):
    sector = department(client, admin, "Setor rebaixamento")
    person = employee(client, admin, job(client, admin, "Supervisor Operacional")["id"], sector)
    _, headers = account(client, admin, employee_id=person["id"])
    assert "orders.assign" in context(client, headers)["permissions"]
    checked(update_employee(client, admin, person, job_position_id=job(client, admin, "Analista de Operações")["id"]))
    actual = context(client, headers)
    assert actual["profile"]["name"] == "Analista"
    assert "orders.assign" not in actual["permissions"]
    records = checked(client.get("/api/audit", headers=admin))
    matching = [row for row in records if str(row.get("entity_id")) == str(person["id"]) and "employee" in row["entity"]]
    assert any("Supervisor" in json.dumps(row, ensure_ascii=False) and "Analista" in json.dumps(row, ensure_ascii=False) for row in matching)


def test_linked_employee_cannot_override_profile_scope_or_legacy_role(client, admin):
    sector = department(client, admin, "Setor sem ambiguidade")
    person = employee(client, admin, job(client, admin, "Atendente")["id"], sector)
    payload = {"name": "Atendente", "email": "override@example.com", "password": PASSWORD, "employee_id": person["id"]}
    administrator = profile(client, admin, "Administrador")
    for extra in ({"profile_id": administrator["id"]}, {"scope": "ALL"}, {"role": "Administrador"}, {"permissions": ["roles.manage"]}):
        assert client.post("/api/users", headers=admin, json={**payload, **extra}).status_code == 422, extra
    created, headers = account(client, admin, employee_id=person["id"])
    assert client.put(f"/api/users/{created['id']}", headers=admin, json={"profile_id": administrator["id"]}).status_code == 422
    assert context(client, headers)["profile"]["name"] == "Atendente"


def test_job_relink_requires_confirmation_and_updates_all_affected_users(client, admin):
    sector = department(client, admin, "Setor impacto")
    position = custom_job(client, admin, profile(client, admin, "Supervisor"))
    accounts = []
    for _ in range(2):
        person = employee(client, admin, position["id"], sector)
        accounts.append(account(client, admin, employee_id=person["id"])[1])
    current = next(row for row in checked(client.get("/api/job-positions", headers=admin)) if row["id"] == position["id"])
    new_profile = profile(client, admin, "Gerente")
    payload = job_payload(current, profile_id=new_profile["id"])
    denied = checked(client.put(f"/api/job-positions/{position['id']}", headers=admin, json=payload), 409)
    assert denied["detail"]["affected_users"] == 2
    assert all(context(client, headers)["profile"]["name"] == "Supervisor" for headers in accounts)
    checked(client.put(f"/api/job-positions/{position['id']}", headers=admin, json={**payload, "confirm_affected_users": True}))
    assert all(context(client, headers)["profile"]["name"] == "Gerente" for headers in accounts)


def test_only_administrator_with_roles_manage_can_change_job_profile(client, admin):
    manager = profile(client, admin, "Gerente")
    created, manager_headers = account(client, admin, profile_id=manager["id"])
    position = job(client, admin, "Atendente")
    payload = job_payload(position, profile_id=profile(client, admin, "Administrador")["id"], confirm_affected_users=True)
    assert client.put(f"/api/job-positions/{position['id']}", headers=manager_headers, json=payload).status_code == 403
    # Even an explicit exception cannot turn a non-administrator into the system administrator.
    assert client.put(f"/api/users/{created['id']}/permissions", headers=admin,
        json={"permissions": {"roles.manage": True}, "reason": "Tentativa de promoção indevida"}).status_code in (403, 422)
    administrator = profile(client, admin, "Administrador")
    restricted_admin, restricted_headers = account(client, admin, profile_id=administrator["id"])
    checked(client.put(f"/api/users/{restricted_admin['id']}/permissions", headers=admin,
        json={"permissions": {"roles.manage": False}, "reason": "Administrador sem gestão de cargos"}))
    assert context(client, restricted_headers)["is_admin"] is True
    assert client.put(f"/api/job-positions/{position['id']}", headers=restricted_headers, json=payload).status_code == 403


def test_last_access_administrator_cannot_be_locked_out(client, admin):
    administrator = profile(client, admin, "Administrador")
    permissions = [code for code in administrator["permissions"] if code != "roles.manage"]
    assert client.put(f"/api/profiles/{administrator['id']}", headers=admin,
        json=profile_payload(administrator, permissions=permissions, confirm_affected_users=True)).status_code == 409
    assert "roles.manage" in context(client, admin)["permissions"]


def test_custom_profile_has_only_explicit_permissions_and_live_edits(client, admin):
    created_profile = custom_profile(client, admin, ["dashboard.view", "orders.view"], "OWN")
    position = custom_job(client, admin, created_profile, "OWN")
    person = employee(client, admin, position["id"], department(client, admin, "Perfil personalizado"))
    _, headers = account(client, admin, employee_id=person["id"])
    actual = context(client, headers)
    assert actual["profile"]["name"] == created_profile["name"]
    assert set(actual["permissions"]) == {"dashboard.view", "orders.view"}
    assert client.get("/api/customers", headers=headers).status_code == 403
    current = profile(client, admin, created_profile["name"])
    checked(client.put(f"/api/profiles/{current['id']}", headers=admin,
        json=profile_payload(current, permissions=["dashboard.view", "customers.view"], confirm_affected_users=True)))
    assert client.get("/api/orders", headers=headers).status_code == 403
    checked(client.get("/api/customers", headers=headers))
    assert "orders.view" not in context(client, headers)["permissions"]


def test_disabled_module_wins_over_profile_and_exception(client, admin):
    finance = profile(client, admin, "Financeiro")
    _, headers = account(client, admin, profile_id=finance["id"])
    checked(client.put("/api/settings/modules", headers=admin, json={**ALL_OFF, "financial": True}))
    checked(client.get("/api/payments", headers=headers))
    assert "financial.view" in context(client, headers)["permissions"]
    checked(client.put("/api/settings/modules", headers=admin, json=ALL_OFF))
    assert client.get("/api/payments", headers=headers).status_code == 404
    assert client.get("/api/module-data/financial/charges", headers=headers).status_code == 404
    assert "financial.view" not in context(client, headers)["permissions"]
    assert checked(client.get("/api/dashboard", headers=headers))["financial"] is None


@pytest.mark.parametrize("scope,allowed", [("OWN", {0}), ("DEPARTMENT", {0, 1}), ("MANAGED_DEPARTMENTS", {0, 1, 2}), ("ALL", {0, 1, 2, 3})])
def test_scopes_filter_lists_details_dashboard_reports_search_and_exports(client, admin, scope, allowed):
    world = order_fixture(client, admin, scope=scope, profile_name="Gerente")
    headers, rows = world["headers"], world["rows"]
    expected = {rows[index]["id"] for index in allowed}
    actual = checked(client.get("/api/orders", headers=headers))
    assert {row["id"] for row in actual} & {row["id"] for row in rows} == expected
    if scope != "ALL":
        assert {row["id"] for row in actual} == expected
    for index, row in enumerate(rows):
        assert client.get(f"/api/orders/{row['id']}", headers=headers).status_code == (200 if index in allowed else 404)
    dashboard = checked(client.get("/api/dashboard", headers=headers))
    assert dashboard["core"]["orders"]["total"] == len(actual)
    reports = checked(client.get("/api/reports", headers=headers))
    assert reports["core"]["overview"]["orders"] == len(actual)
    search = checked(client.get("/api/search", headers=headers, params={"q": world["marker"]}))
    assert {row["id"] for row in search if row["type"] == "order"} == expected
    exported = client.get("/api/reports/orders.csv", headers=headers)
    assert exported.status_code == 200, exported.text
    for index, row in enumerate(rows):
        assert (row["number"] in exported.text) == (index in allowed)
        assert client.get(f"/api/customers/{row['customer_id']}", headers=headers).status_code == (200 if index in allowed else 404)
    visible_people = checked(client.get("/api/catalog", headers=headers))["employees"]
    assert not ({world["people"][index]["id"] for index in range(4) if index not in allowed} & {row["id"] for row in visible_people})


def test_scope_change_takes_effect_with_same_jwt_and_requires_confirmation(client, admin):
    world = order_fixture(client, admin)
    headers = world["headers"]
    assert len(checked(client.get("/api/orders", headers=headers))) == 2
    current = next(row for row in checked(client.get("/api/job-positions", headers=admin)) if row["id"] == world["job"]["id"])
    payload = job_payload(current, default_scope="OWN")
    assert client.put(f"/api/job-positions/{current['id']}", headers=admin, json=payload).status_code == 409
    checked(client.put(f"/api/job-positions/{current['id']}", headers=admin, json={**payload, "confirm_affected_users": True}))
    assert context(client, headers)["scope"] == "OWN"
    assert [row["id"] for row in checked(client.get("/api/orders", headers=headers))] == [world["rows"][0]["id"]]


def test_supervisor_cannot_move_orders_or_assign_tasks_outside_scope(client, admin):
    world = order_fixture(client, admin)
    row = world["rows"][0]
    payload = {key: value for key, value in row.items() if key not in ("id", "number")}
    outside = world["people"][2]
    denied = client.put(f"/api/orders/{row['id']}", headers=world["headers"], json={**payload, "assignee_id": outside["id"], "department_id": outside["department_id"]})
    assert denied.status_code in (403, 404)
    denied = client.post(f"/api/orders/{row['id']}/tasks", headers=world["headers"], json={"title": "Atribuição externa", "assignee_id": outside["id"]})
    assert denied.status_code in (403, 404)
    detail = checked(client.get(f"/api/orders/{row['id']}", headers=admin))
    assert detail["assignee"]["id"] == world["people"][0]["id"]
    assert not detail["tasks"]


def test_standard_profiles_and_jobs_have_unambiguous_financial_mapping(client, admin):
    profiles = checked(client.get("/api/profiles", headers=admin))
    assert {"Administrador", "Gerente", "Supervisor", "Analista", "Atendente", "Operacional", "Financeiro", "Visualizador"} <= {row["name"] for row in profiles}
    positions = checked(client.get("/api/job-positions", headers=admin))
    by_id = {row["id"]: row["name"] for row in profiles}
    assert by_id[job(client, admin, "Analista Financeiro")["profile_id"]] == "Financeiro"
    assert all(row["profile_id"] in by_id for row in positions)
    assert len({row["name"] for row in positions}) == len(positions)

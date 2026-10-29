import pytest
from fastapi.testclient import TestClient
from app import app, calculate_total, PRODUCTS, ORDERS

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    initial_stock = {product_id: product["stock"] for product_id, product in PRODUCTS.items()}
    ORDERS.clear()
    yield
    ORDERS.clear()
    for product_id, stock in initial_stock.items():
        PRODUCTS[product_id]["stock"] = stock


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_discount():
    assert calculate_total(100.0, 0.10) == 90.0


def test_create_order():
    response = client.post("/orders", json={
        "customer_id": "CUST-1001",
        "items": [{"product_id": "LAPTOP-001", "quantity": 1}],
        "discount_code": "SAVE10",
    })
    assert response.status_code == 201
    assert response.json()["total"] == 900.0
    assert response.json()["status"] == "accepted"


def test_insufficient_inventory():
    response = client.post("/orders", json={
        "customer_id": "CUST-1002",
        "items": [{"product_id": "LAPTOP-001", "quantity": 100}],
    })
    assert response.status_code == 400


def test_unknown_product():
    response = client.post("/orders", json={
        "customer_id": "CUST-1003",
        "items": [{"product_id": "UNKNOWN", "quantity": 1}],
    })
    assert response.status_code == 404


def test_get_order():
    created = client.post("/orders", json={
        "customer_id": "CUST-1004",
        "items": [{"product_id": "MOUSE-001", "quantity": 2}],
    })
    assert created.status_code == 201
    response = client.get(f"/orders/{created.json()['order_id']}")
    assert response.status_code == 200
    assert response.json()["total"] == 50.0


def test_duplicate_items_cannot_exceed_inventory():
    response = client.post("/orders", json={
        "customer_id": "CUST-1005",
        "items": [
            {"product_id": "LAPTOP-001", "quantity": 6},
            {"product_id": "LAPTOP-001", "quantity": 6},
        ],
    })
    assert response.status_code == 400

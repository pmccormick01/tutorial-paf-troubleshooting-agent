from uuid import uuid4
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Order Service", version="1.0.0")

PRODUCTS = {
    "LAPTOP-001": {"price": 1000.00, "stock": 10},
    "MOUSE-001": {"price": 25.00, "stock": 50},
    "KEYBOARD-001": {"price": 75.00, "stock": 25},
}
DISCOUNTS = {"SAVE10": 0.10, "SAVE20": 0.20}
ORDERS = {}


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class OrderRequest(BaseModel):
    customer_id: str
    items: list[OrderItem]
    discount_code: str | None = None


def calculate_total(subtotal: float, discount: float) -> float:
    return round(subtotal * (1 - discount), 2)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/orders", status_code=201)
def create_order(order: OrderRequest):
    if not order.items:
        raise HTTPException(400, "Order must contain items")

    subtotal = 0.0
    quantities = {}
    for item in order.items:
        product = PRODUCTS.get(item.product_id)
        if product is None:
            raise HTTPException(404, f"Unknown product: {item.product_id}")
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
        if quantities[item.product_id] > product["stock"]:
            raise HTTPException(400, "Insufficient inventory")
        subtotal += product["price"] * item.quantity

    if order.discount_code and order.discount_code not in DISCOUNTS:
        raise HTTPException(400, "Invalid discount code")

    discount = DISCOUNTS.get(order.discount_code, 0.0)
    result = {
        "order_id": f"ORD-{uuid4().hex[:8].upper()}",
        "customer_id": order.customer_id,
        "subtotal": round(subtotal, 2),
        "discount": discount,
        "total": calculate_total(subtotal, discount),
        "status": "accepted",
    }
    for product_id, quantity in quantities.items():
        PRODUCTS[product_id]["stock"] -= quantity
    ORDERS[result["order_id"]] = result
    return result


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in ORDERS:
        raise HTTPException(404, "Order not found")
    return ORDERS[order_id]

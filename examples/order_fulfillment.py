"""
An order-fulfillment workflow: more than a straight line of steps.

    validate_order --valid--> check_inventory --in stock--> charge_payment --success--> ship_order --> notify_shipped
         |--invalid--> reject_order            |--out of stock--> notify_backorder        |--error, retries left--(loops back to itself)
                                                                                            |--otherwise (declined, or retries exhausted)--> notify_payment_failed

Run it directly: .venv/bin/python examples/order_fulfillment.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_machine import StateMachine  # noqa: E402

DB_PATH = Path(__file__).parent / ".order_fulfillment_demo.db"
DB_PATH.unlink(missing_ok=True)

machine = StateMachine(db_path=str(DB_PATH), max_steps=100)


@machine.step("validate_order")
def validate_order(ctx):
    ctx["valid"] = bool(ctx.get("items")) and bool(ctx.get("shipping_address"))
    if not ctx["valid"]:
        ctx["rejection_reason"] = "missing items or shipping address"
    return ctx


@machine.step("check_inventory")
def check_inventory(ctx):
    # stand-in for a real warehouse/inventory lookup
    unavailable = [item for item in ctx["items"] if item in ctx.get("_out_of_stock", [])]
    ctx["in_stock"] = not unavailable
    ctx["unavailable_items"] = unavailable
    return ctx


@machine.step("charge_payment")
def charge_payment(ctx):
    ctx["payment_attempts"] = ctx.get("payment_attempts", 0) + 1
    # stand-in for a real payment gateway call; scripted outcomes drive the demo
    outcomes = ctx.get("_payment_outcomes", ["success"])
    outcome = outcomes[min(ctx["payment_attempts"] - 1, len(outcomes) - 1)]
    ctx["payment_status"] = outcome
    print(f"  [charge_payment] attempt {ctx['payment_attempts']} -> {outcome}")
    return ctx


@machine.step("ship_order")
def ship_order(ctx):
    ctx["tracking_number"] = f"TRK-{ctx['order_id']}"
    return ctx


@machine.step("notify_shipped")
def notify_shipped(ctx):
    print(f"  [notify] order {ctx['order_id']} shipped, tracking {ctx['tracking_number']}")
    return ctx


@machine.step("reject_order")
def reject_order(ctx):
    print(f"  [notify] order {ctx['order_id']} rejected: {ctx['rejection_reason']}")
    return ctx


@machine.step("notify_backorder")
def notify_backorder(ctx):
    print(f"  [notify] order {ctx['order_id']} backordered, unavailable: {ctx['unavailable_items']}")
    return ctx


@machine.step("notify_payment_failed")
def notify_payment_failed(ctx):
    print(
        f"  [notify] order {ctx['order_id']} payment failed after "
        f"{ctx['payment_attempts']} attempt(s), last status: {ctx['payment_status']}"
    )
    return ctx


validate_order.to(check_inventory, when=lambda ctx: ctx["valid"])
validate_order.otherwise(reject_order)

check_inventory.to(charge_payment, when=lambda ctx: ctx["in_stock"])
check_inventory.otherwise(notify_backorder)

charge_payment.to(ship_order, when=lambda ctx: ctx["payment_status"] == "success")
charge_payment.to(
    charge_payment,
    when=lambda ctx: ctx["payment_status"] == "error" and ctx["payment_attempts"] < 3,
)
charge_payment.otherwise(notify_payment_failed)  # declined, or errored out of retries

ship_order.otherwise(notify_shipped)


def run_status(run_id):
    return next(r for r in machine.list_runs() if r["run_id"] == run_id)


SCENARIOS = [
    (
        "happy path",
        {
            "order_id": "A100",
            "items": ["mug"],
            "shipping_address": "1 Main St",
            "_payment_outcomes": ["success"],
        },
    ),
    (
        "invalid order (no shipping address)",
        {
            "order_id": "A101",
            "items": ["mug"],
            "shipping_address": None,
        },
    ),
    (
        "item out of stock",
        {
            "order_id": "A102",
            "items": ["mug", "hat"],
            "shipping_address": "1 Main St",
            "_out_of_stock": ["hat"],
        },
    ),
    (
        "payment errors twice, then succeeds (retry loop)",
        {
            "order_id": "A103",
            "items": ["mug"],
            "shipping_address": "1 Main St",
            "_payment_outcomes": ["error", "error", "success"],
        },
    ),
    (
        "payment declined",
        {
            "order_id": "A104",
            "items": ["mug"],
            "shipping_address": "1 Main St",
            "_payment_outcomes": ["declined"],
        },
    ),
]


if __name__ == "__main__":
    for label, context in SCENARIOS:
        print(f"\n=== {label} ===")
        run_id = machine.run(context)
        status = run_status(run_id)
        print(f"  -> ended at '{status['current_step']}' with status '{status['status']}'")

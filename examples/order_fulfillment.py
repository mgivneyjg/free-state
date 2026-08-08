"""
An order-fulfillment workflow: more than a straight line of steps.

    validate_order --valid--> check_inventory --in stock--> charge_payment --success--> ship_order --> notify_shipped
         |--invalid--> reject_order            |--out of stock--> notify_backorder        |--error, retries left--(loops back to itself)
                                                                                            |--otherwise (declined, or retries exhausted)--> notify_payment_failed

Run it directly: .venv/bin/python examples/order_fulfillment.py
"""

import dataclasses
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_machine import StateMachine  # noqa: E402

DB_PATH = Path(__file__).parent / ".order_fulfillment_demo.db"
DB_PATH.unlink(missing_ok=True)


@dataclass(frozen=True)
class OrderContext:
    order_id: str
    items: list[str]
    shipping_address: Optional[str]

    # scripted responses from external systems, for demo purposes only
    out_of_stock: list[str] = field(default_factory=list)
    payment_outcomes: list[str] = field(default_factory=lambda: ["success"])

    # fields the steps below fill in as the order moves through the machine
    valid: bool = False
    rejection_reason: Optional[str] = None
    in_stock: bool = False
    unavailable_items: list[str] = field(default_factory=list)
    payment_attempts: int = 0
    payment_status: Optional[str] = None
    tracking_number: Optional[str] = None


machine = StateMachine(context_type=OrderContext, db_path=str(DB_PATH), max_steps=100)


@machine.step("validate_order")
def validate_order(ctx: OrderContext) -> OrderContext:
    valid = bool(ctx.items) and bool(ctx.shipping_address)
    return dataclasses.replace(
        ctx,
        valid=valid,
        rejection_reason=None if valid else "missing items or shipping address",
    )


@machine.step("check_inventory")
def check_inventory(ctx: OrderContext) -> OrderContext:
    # stand-in for a real warehouse/inventory lookup
    unavailable = [item for item in ctx.items if item in ctx.out_of_stock]
    return dataclasses.replace(ctx, in_stock=not unavailable, unavailable_items=unavailable)


@machine.step("charge_payment")
def charge_payment(ctx: OrderContext) -> OrderContext:
    attempts = ctx.payment_attempts + 1
    # stand-in for a real payment gateway call; scripted outcomes drive the demo
    outcome = ctx.payment_outcomes[min(attempts - 1, len(ctx.payment_outcomes) - 1)]
    print(f"  [charge_payment] attempt {attempts} -> {outcome}")
    return dataclasses.replace(ctx, payment_attempts=attempts, payment_status=outcome)


@machine.step("ship_order")
def ship_order(ctx: OrderContext) -> OrderContext:
    return dataclasses.replace(ctx, tracking_number=f"TRK-{ctx.order_id}")


@machine.step("notify_shipped")
def notify_shipped(ctx: OrderContext) -> OrderContext:
    print(f"  [notify] order {ctx.order_id} shipped, tracking {ctx.tracking_number}")
    return ctx


@machine.step("reject_order")
def reject_order(ctx: OrderContext) -> OrderContext:
    print(f"  [notify] order {ctx.order_id} rejected: {ctx.rejection_reason}")
    return ctx


@machine.step("notify_backorder")
def notify_backorder(ctx: OrderContext) -> OrderContext:
    print(f"  [notify] order {ctx.order_id} backordered, unavailable: {ctx.unavailable_items}")
    return ctx


@machine.step("notify_payment_failed")
def notify_payment_failed(ctx: OrderContext) -> OrderContext:
    print(
        f"  [notify] order {ctx.order_id} payment failed after "
        f"{ctx.payment_attempts} attempt(s), last status: {ctx.payment_status}"
    )
    return ctx


validate_order.to(check_inventory, when=lambda ctx: ctx.valid)
validate_order.otherwise(reject_order)

check_inventory.to(charge_payment, when=lambda ctx: ctx.in_stock)
check_inventory.otherwise(notify_backorder)

charge_payment.to(ship_order, when=lambda ctx: ctx.payment_status == "success")
charge_payment.to(
    charge_payment,
    when=lambda ctx: ctx.payment_status == "error" and ctx.payment_attempts < 3,
)
charge_payment.otherwise(notify_payment_failed)  # declined, or errored out of retries

ship_order.otherwise(notify_shipped)


def run_status(run_id):
    return next(r for r in machine.list_runs() if r["run_id"] == run_id)


SCENARIOS = [
    (
        "happy path",
        OrderContext(order_id="A100", items=["mug"], shipping_address="1 Main St"),
    ),
    (
        "invalid order (no shipping address)",
        OrderContext(order_id="A101", items=["mug"], shipping_address=None),
    ),
    (
        "item out of stock",
        OrderContext(
            order_id="A102",
            items=["mug", "hat"],
            shipping_address="1 Main St",
            out_of_stock=["hat"],
        ),
    ),
    (
        "payment errors twice, then succeeds (retry loop)",
        OrderContext(
            order_id="A103",
            items=["mug"],
            shipping_address="1 Main St",
            payment_outcomes=["error", "error", "success"],
        ),
    ),
    (
        "payment declined",
        OrderContext(
            order_id="A104",
            items=["mug"],
            shipping_address="1 Main St",
            payment_outcomes=["declined"],
        ),
    ),
]


if __name__ == "__main__":
    for label, context in SCENARIOS:
        print(f"\n=== {label} ===")
        run_id = machine.run(context)
        status = run_status(run_id)
        print(f"  -> ended at '{status['current_step']}' with status '{status['status']}'")

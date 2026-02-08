from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Box,
    Order,
    OrderItem,
    PackingExecutionLog,
    PackingPlan,
    PackingShipment,
    PackingShipmentItem,
    ProhibitedGroupPair,
    ShippingRate,
    SKU,
)

SEED_FILE_NAMES = {
    "skus": "skus.csv",
    "boxes": "boxes.csv",
    "shipping_rates": "shipping_rates.csv",
    "orders": "orders.csv",
    "order_items": "order_items.csv",
    "prohibited": "prohibited_group_pairs.csv",
}

REQUIRED_COLUMNS = {
    "skus.csv": [
        "sku_id",
        "name",
        "category",
        "length_mm",
        "width_mm",
        "height_mm",
        "weight_g",
        "can_rotate",
        "fragile",
        "compressible",
        "hazmat",
        "padding_mm",
        "prohibited_group",
    ],
    "boxes.csv": [
        "box_id",
        "name",
        "inner_length_mm",
        "inner_width_mm",
        "inner_height_mm",
        "max_weight_g",
        "box_cost_yen",
        "box_type",
        "outer_length_mm",
        "outer_width_mm",
        "outer_height_mm",
    ],
    "shipping_rates.csv": ["carrier", "service", "size_class", "max_weight_g", "price_yen"],
    "prohibited_group_pairs.csv": ["group_a", "group_b", "reason"],
    "orders.csv": ["order_id", "order_date", "channel", "destination_prefecture", "status", "customer_note"],
    "order_items.csv": ["order_id", "sku_id", "qty"],
}


def _to_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return int(float(text))


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _norm(value: str | None) -> str:
    return (value or "").strip()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_csv_rows_from_bytes(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def ensure_required_columns(rows: list[dict[str, str]], csv_name: str) -> None:
    required = REQUIRED_COLUMNS.get(csv_name)
    if not required:
        return
    if not rows:
        raise ValueError(f"{csv_name} が空です")

    available = set((rows[0] or {}).keys())
    missing = [column for column in required if column not in available]
    if missing:
        missing_label = ", ".join(missing)
        raise ValueError(f"{csv_name} のヘッダーが不正です。必要列: {missing_label}")


def upsert_skus(db: Session, rows: Iterable[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        sku_id = _norm(row.get("sku_id"))
        if not sku_id:
            continue
        sku = db.get(SKU, sku_id)
        if not sku:
            sku = SKU(sku_id=sku_id)
            db.add(sku)

        sku.name = _norm(row.get("name")) or sku_id
        sku.category = _norm(row.get("category")) or "other"
        sku.length_mm = _to_int(row.get("length_mm"))
        sku.width_mm = _to_int(row.get("width_mm"))
        sku.height_mm = _to_int(row.get("height_mm"))
        sku.weight_g = _to_int(row.get("weight_g"))
        sku.can_rotate = _to_bool(row.get("can_rotate"), True)
        sku.fragile = _to_bool(row.get("fragile"), False)
        sku.compressible = _to_bool(row.get("compressible"), False)
        sku.hazmat = _to_bool(row.get("hazmat"), False)
        sku.padding_mm = _to_int(row.get("padding_mm"), 0)
        sku.prohibited_group = _norm(row.get("prohibited_group")) or None
        count += 1
    return count


def upsert_boxes(db: Session, rows: Iterable[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        box_id = _norm(row.get("box_id"))
        if not box_id:
            continue

        box = db.get(Box, box_id)
        if not box:
            box = Box(box_id=box_id)
            db.add(box)

        box.name = _norm(row.get("name")) or box_id
        box.inner_length_mm = _to_int(row.get("inner_length_mm"))
        box.inner_width_mm = _to_int(row.get("inner_width_mm"))
        box.inner_height_mm = _to_int(row.get("inner_height_mm"))
        box.max_weight_g = _to_int(row.get("max_weight_g"))
        box.box_cost_yen = _to_int(row.get("box_cost_yen"))
        box.box_type = _norm(row.get("box_type")) or "box"
        box.outer_length_mm = _to_int(row.get("outer_length_mm"))
        box.outer_width_mm = _to_int(row.get("outer_width_mm"))
        box.outer_height_mm = _to_int(row.get("outer_height_mm"))
        count += 1
    return count


def replace_shipping_rates(db: Session, rows: Iterable[dict[str, str]]) -> int:
    db.query(ShippingRate).delete()
    count = 0
    for row in rows:
        carrier = _norm(row.get("carrier"))
        service = _norm(row.get("service"))
        size_class = _norm(row.get("size_class"))
        if not carrier or not service or not size_class:
            continue
        rate = ShippingRate(
            carrier=carrier,
            service=service,
            size_class=size_class,
            max_weight_g=_to_int(row.get("max_weight_g")),
            price_yen=_to_int(row.get("price_yen")),
        )
        db.add(rate)
        count += 1
    return count


def replace_prohibited_pairs(db: Session, rows: Iterable[dict[str, str]]) -> int:
    db.query(ProhibitedGroupPair).delete()
    count = 0
    for row in rows:
        a = _norm(row.get("group_a"))
        b = _norm(row.get("group_b"))
        reason = _norm(row.get("reason")) or "同梱不可"
        if not a or not b:
            continue
        db.add(ProhibitedGroupPair(group_a=a, group_b=b, reason=reason))
        count += 1
    return count


def upsert_orders(db: Session, rows: Iterable[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        order_id = _norm(row.get("order_id"))
        if not order_id:
            continue
        date_text = _norm(row.get("order_date"))
        order_date = datetime.strptime(date_text, "%Y-%m-%d").date() if date_text else datetime.utcnow().date()
        db.add(
            Order(
                order_id=order_id,
                order_date=order_date,
                channel=_norm(row.get("channel")) or "EC",
                destination_prefecture=_norm(row.get("destination_prefecture")) or "東京都",
                status=_norm(row.get("status")) or "created",
                customer_note=_norm(row.get("customer_note")) or None,
            )
        )
        count += 1
    return count


def replace_orders(db: Session, rows: Iterable[dict[str, str]]) -> int:
    db.query(Order).delete()
    return upsert_orders(db, rows)


def upsert_order_items(db: Session, rows: Iterable[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        order_id = _norm(row.get("order_id"))
        sku_id = _norm(row.get("sku_id"))
        qty = _to_int(row.get("qty"), 0)
        if not order_id or not sku_id or qty <= 0:
            continue
        db.add(OrderItem(order_id=order_id, sku_id=sku_id, qty=qty))
        count += 1
    return count


def replace_order_items(db: Session, rows: Iterable[dict[str, str]]) -> int:
    db.query(OrderItem).delete()
    return upsert_order_items(db, rows)


def clear_all_data(db: Session) -> None:
    db.query(PackingExecutionLog).delete()
    db.query(PackingShipmentItem).delete()
    db.query(PackingShipment).delete()
    db.query(PackingPlan).delete()
    db.query(OrderItem).delete()
    db.query(Order).delete()
    db.query(ProhibitedGroupPair).delete()
    db.query(ShippingRate).delete()
    db.query(Box).delete()
    db.query(SKU).delete()


def clear_order_data(db: Session) -> None:
    db.query(PackingExecutionLog).delete()
    db.query(PackingShipmentItem).delete()
    db.query(PackingShipment).delete()
    db.query(PackingPlan).delete()
    db.query(OrderItem).delete()
    db.query(Order).delete()


def clear_order_data_for_orders(db: Session, order_ids: Sequence[str]) -> None:
    targets = sorted({order_id for order_id in order_ids if order_id})
    if not targets:
        return

    plan_ids = select(PackingPlan.id).where(PackingPlan.order_id.in_(targets))
    shipment_ids = select(PackingShipment.id).where(PackingShipment.plan_id.in_(plan_ids))

    db.query(PackingExecutionLog).filter(PackingExecutionLog.order_id.in_(targets)).delete(synchronize_session=False)
    db.query(PackingShipmentItem).filter(PackingShipmentItem.shipment_id.in_(shipment_ids)).delete(synchronize_session=False)
    db.query(PackingShipment).filter(PackingShipment.plan_id.in_(plan_ids)).delete(synchronize_session=False)
    db.query(PackingPlan).filter(PackingPlan.order_id.in_(targets)).delete(synchronize_session=False)
    db.query(OrderItem).filter(OrderItem.order_id.in_(targets)).delete(synchronize_session=False)
    db.query(Order).filter(Order.order_id.in_(targets)).delete(synchronize_session=False)


def seed_if_empty(db: Session, seed_dir: Path | None = None, force: bool = False) -> bool:
    seed_base = seed_dir or Path("seed")
    if not force:
        has_data = db.scalar(select(SKU.sku_id).limit(1)) is not None
        if has_data:
            return False

    clear_all_data(db)

    skus = read_csv_rows(seed_base / SEED_FILE_NAMES["skus"])
    boxes = read_csv_rows(seed_base / SEED_FILE_NAMES["boxes"])
    rates = read_csv_rows(seed_base / SEED_FILE_NAMES["shipping_rates"])
    orders = read_csv_rows(seed_base / SEED_FILE_NAMES["orders"])
    order_items = read_csv_rows(seed_base / SEED_FILE_NAMES["order_items"])
    prohibited = read_csv_rows(seed_base / SEED_FILE_NAMES["prohibited"])

    upsert_skus(db, skus)
    upsert_boxes(db, boxes)
    replace_shipping_rates(db, rates)
    replace_prohibited_pairs(db, prohibited)
    replace_orders(db, orders)
    replace_order_items(db, order_items)

    db.commit()
    return True

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from itertools import permutations
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Box,
    Order,
    OrderItem,
    PackingPlan,
    PackingShipment,
    PackingShipmentItem,
    ProhibitedGroupPair,
    ShippingRate,
    SKU,
)

SIZE_THRESHOLDS = [60, 80, 100, 120, 140, 160]


@dataclass
class ExpandedItem:
    sku_id: str
    name: str
    category: str
    can_rotate: bool
    fragile: bool
    prohibited_group: str | None
    weight_g: int
    effective_dims: tuple[int, int, int]

    @property
    def volume(self) -> int:
        l, w, h = self.effective_dims
        return l * w * h


@dataclass
class WorkingShipment:
    items: list[ExpandedItem]
    groups: set[str]
    split_reasons: set[str]


@dataclass
class Candidate:
    box_id: str
    box_name: str
    carrier: str
    service: str
    size_class: str
    shipping_yen: int
    box_cost_yen: int
    total_cost_yen: int
    fill_ratio: float
    warning: str | None
    box_volume: int


def effective_dims(sku: SKU) -> tuple[int, int, int]:
    pad = max(0, int(sku.padding_mm))
    return (
        int(sku.length_mm) + 2 * pad,
        int(sku.width_mm) + 2 * pad,
        int(sku.height_mm) + 2 * pad,
    )


def expand_order_items(order_items: Sequence[OrderItem]) -> list[ExpandedItem]:
    expanded: list[ExpandedItem] = []
    for item in order_items:
        if not item.sku:
            continue
        dims = effective_dims(item.sku)
        for _ in range(max(0, int(item.qty))):
            expanded.append(
                ExpandedItem(
                    sku_id=item.sku_id,
                    name=item.sku.name,
                    category=item.sku.category,
                    can_rotate=bool(item.sku.can_rotate),
                    fragile=bool(item.sku.fragile),
                    prohibited_group=item.sku.prohibited_group or None,
                    weight_g=int(item.sku.weight_g),
                    effective_dims=dims,
                )
            )
    return expanded


def _prohibited_index(pairs: Iterable[ProhibitedGroupPair]) -> dict[frozenset[str], str]:
    index: dict[frozenset[str], str] = {}
    for pair in pairs:
        key = frozenset({pair.group_a, pair.group_b})
        index[key] = pair.reason
    return index


def _conflict_reason(groups: set[str], new_group: str | None, pair_index: dict[frozenset[str], str]) -> str | None:
    if not new_group:
        return None
    for group in groups:
        reason = pair_index.get(frozenset({group, new_group}))
        if reason:
            return reason
    return None


def split_shipments(expanded_items: Sequence[ExpandedItem], pairs: Sequence[ProhibitedGroupPair]) -> list[WorkingShipment]:
    pair_index = _prohibited_index(pairs)
    shipments: list[WorkingShipment] = []

    sorted_items = sorted(
        expanded_items,
        key=lambda x: (x.prohibited_group or "", -x.volume),
    )

    for unit in sorted_items:
        placed = False
        for shipment in shipments:
            reason = _conflict_reason(shipment.groups, unit.prohibited_group, pair_index)
            if reason:
                continue
            shipment.items.append(unit)
            if unit.prohibited_group:
                shipment.groups.add(unit.prohibited_group)
            placed = True
            break

        if placed:
            continue

        conflict_reasons: set[str] = set()
        for shipment in shipments:
            reason = _conflict_reason(shipment.groups, unit.prohibited_group, pair_index)
            if reason:
                conflict_reasons.add(reason)

        shipments.append(
            WorkingShipment(
                items=[unit],
                groups={unit.prohibited_group} if unit.prohibited_group else set(),
                split_reasons=conflict_reasons,
            )
        )

    if len(shipments) > 1:
        all_reasons = sorted({reason for shipment in shipments for reason in shipment.split_reasons if reason})
        if all_reasons:
            joined = " / ".join(all_reasons)
            for shipment in shipments:
                shipment.split_reasons.add(joined)

    return shipments


def _item_fits_box(item: ExpandedItem, box: Box) -> bool:
    inside = (int(box.inner_length_mm), int(box.inner_width_mm), int(box.inner_height_mm))
    dims = item.effective_dims

    if item.can_rotate:
        for oriented in set(permutations(dims, 3)):
            if oriented[0] <= inside[0] and oriented[1] <= inside[1] and oriented[2] <= inside[2]:
                return True
        return False

    return dims[0] <= inside[0] and dims[1] <= inside[1] and dims[2] <= inside[2]


def _size_class_for_box(box: Box) -> str | None:
    if box.box_type == "mailer":
        return "MAIL"

    sum_cm = (
        math.ceil(int(box.outer_length_mm) / 10)
        + math.ceil(int(box.outer_width_mm) / 10)
        + math.ceil(int(box.outer_height_mm) / 10)
    )
    for threshold in SIZE_THRESHOLDS:
        if sum_cm <= threshold:
            return str(threshold)
    return None


def _service_for_size(size_class: str) -> str:
    if size_class == "MAIL":
        return "Mail"
    return "Economy"


def _pick_rate(
    rates: Sequence[ShippingRate],
    carrier_preference: str,
    service: str,
    size_class: str,
    total_weight_g: int,
) -> ShippingRate | None:
    available = [
        rate
        for rate in rates
        if rate.service == service and str(rate.size_class) == str(size_class) and total_weight_g <= int(rate.max_weight_g)
    ]
    if not available:
        return None

    preferred = [r for r in available if r.carrier == carrier_preference]
    if preferred:
        return sorted(preferred, key=lambda r: (r.price_yen, r.max_weight_g))[0]

    fallback = [r for r in available if r.carrier == "CarrierA"]
    if fallback:
        return sorted(fallback, key=lambda r: (r.price_yen, r.max_weight_g))[0]

    return sorted(available, key=lambda r: (r.price_yen, r.carrier))[0]


def recommend_candidates(
    items: Sequence[ExpandedItem],
    boxes: Sequence[Box],
    rates: Sequence[ShippingRate],
    carrier_preference: str = "CarrierB",
) -> list[Candidate]:
    if not items:
        return []

    total_weight = sum(item.weight_g for item in items)
    total_volume = sum(item.volume for item in items)
    fragile_exists = any(item.fragile for item in items)
    candidates: list[Candidate] = []

    for box in boxes:
        if total_weight > int(box.max_weight_g):
            continue

        if not all(_item_fits_box(item, box) for item in items):
            continue

        box_volume = int(box.inner_length_mm) * int(box.inner_width_mm) * int(box.inner_height_mm)
        if box_volume <= 0:
            continue

        fill_ratio = total_volume / box_volume
        max_fill_ratio = 0.8 if fragile_exists else 0.9
        if box.box_type == "long":
            max_fill_ratio = min(max_fill_ratio, 0.9)
        if fill_ratio > max_fill_ratio:
            continue

        size_class = _size_class_for_box(box)
        if not size_class:
            continue
        service = _service_for_size(size_class)
        rate = _pick_rate(rates, carrier_preference, service, size_class, total_weight)
        if not rate:
            continue

        shipping_yen = int(rate.price_yen)
        box_cost = int(box.box_cost_yen)
        candidates.append(
            Candidate(
                box_id=box.box_id,
                box_name=box.name,
                carrier=rate.carrier,
                service=service,
                size_class=size_class,
                shipping_yen=shipping_yen,
                box_cost_yen=box_cost,
                total_cost_yen=shipping_yen + box_cost,
                fill_ratio=fill_ratio,
                warning=None,
                box_volume=box_volume,
            )
        )

    candidates.sort(key=lambda c: (c.total_cost_yen, c.fill_ratio, c.box_volume))
    return candidates


def latest_plan(db: Session, order_id: str) -> PackingPlan | None:
    return db.scalar(
        select(PackingPlan)
        .where(PackingPlan.order_id == order_id)
        .order_by(PackingPlan.created_at.desc(), PackingPlan.id.desc())
        .limit(1)
    )


def recalculate_order_plan(db: Session, order_id: str, carrier_preference: str = "CarrierB") -> PackingPlan:
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Order not found: {order_id}")

    order_items = db.scalars(
        select(OrderItem)
        .options(selectinload(OrderItem.sku))
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.id.asc())
    ).all()
    if not order_items:
        raise ValueError("受注明細が存在しません")

    boxes = db.scalars(select(Box).order_by(Box.inner_length_mm.asc(), Box.inner_width_mm.asc())).all()
    rates = db.scalars(select(ShippingRate)).all()
    pairs = db.scalars(select(ProhibitedGroupPair)).all()

    expanded_items = expand_order_items(order_items)
    work_shipments = split_shipments(expanded_items, pairs)

    old_plans = db.scalars(select(PackingPlan).where(PackingPlan.order_id == order_id)).all()
    for old in old_plans:
        db.delete(old)
    db.flush()

    plan = PackingPlan(order_id=order_id)
    db.add(plan)
    db.flush()

    all_split_reasons = sorted({r for s in work_shipments for r in s.split_reasons if r})
    split_reason = " / ".join(all_split_reasons) if all_split_reasons else None

    for idx, work in enumerate(work_shipments, start=1):
        by_sku = Counter(item.sku_id for item in work.items)
        candidates = recommend_candidates(work.items, boxes, rates, carrier_preference)
        best = candidates[0] if candidates else None

        shipment = PackingShipment(
            plan_id=plan.id,
            shipment_no=idx,
            split_reason=split_reason if len(work_shipments) > 1 else None,
            recommended_box_id=best.box_id if best else None,
            recommended_box_name=best.box_name if best else None,
            carrier=best.carrier if best else None,
            service=best.service if best else None,
            size_class=best.size_class if best else None,
            shipping_yen=best.shipping_yen if best else None,
            box_cost_yen=best.box_cost_yen if best else None,
            total_cost_yen=best.total_cost_yen if best else None,
            fill_ratio=best.fill_ratio if best else None,
            warning_message=None if best else "適合する箱が見つかりません",
        )
        db.add(shipment)
        db.flush()

        for sku_id, qty in sorted(by_sku.items()):
            db.add(PackingShipmentItem(shipment_id=shipment.id, sku_id=sku_id, qty=qty))

    db.commit()
    db.refresh(plan)
    return plan


def ensure_order_plan(db: Session, order_id: str, carrier_preference: str = "CarrierB") -> PackingPlan:
    plan = latest_plan(db, order_id)
    if plan:
        return plan
    return recalculate_order_plan(db, order_id, carrier_preference)


def recalculate_all_orders(db: Session, carrier_preference: str = "CarrierB") -> None:
    order_ids = db.scalars(select(Order.order_id).order_by(Order.order_id.asc())).all()
    for order_id in order_ids:
        try:
            recalculate_order_plan(db, order_id, carrier_preference)
        except Exception:
            db.rollback()


def build_virtual_item(
    name: str,
    category: str,
    length_mm: int,
    width_mm: int,
    height_mm: int,
    weight_g: int,
    padding_mm: int,
    can_rotate: bool,
    fragile: bool,
) -> ExpandedItem:
    pad = max(0, int(padding_mm))
    return ExpandedItem(
        sku_id="SIMULATED",
        name=name or "シミュレーションSKU",
        category=category or "other",
        can_rotate=bool(can_rotate),
        fragile=bool(fragile),
        prohibited_group=None,
        weight_g=int(weight_g),
        effective_dims=(int(length_mm) + 2 * pad, int(width_mm) + 2 * pad, int(height_mm) + 2 * pad),
    )


def simulate_top_candidates(
    item: ExpandedItem,
    boxes: Sequence[Box],
    rates: Sequence[ShippingRate],
    carrier_preference: str = "CarrierB",
    limit: int = 5,
) -> list[Candidate]:
    candidates = recommend_candidates([item], boxes, rates, carrier_preference)
    return candidates[:limit]

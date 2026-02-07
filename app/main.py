from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import Base, SessionLocal, engine, get_db
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
from app.services.packing import (
    build_virtual_item,
    ensure_order_plan,
    latest_plan,
    recalculate_all_orders,
    recalculate_order_plan,
    simulate_top_candidates,
)
from app.services.seed import (
    read_csv_rows_from_bytes,
    replace_prohibited_pairs,
    replace_shipping_rates,
    seed_if_empty,
    upsert_boxes,
    upsert_skus,
)

app = FastAPI(title="AI Packing Demo")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

REASON_LABELS = {
    "NO_FIT": "入らない",
    "EXTRA_PADDING": "緩衝材が多い",
    "NO_STOCK": "箱在庫切れ",
    "DAMAGE_RISK": "破損リスク",
    "OPERATION": "オペレーション都合",
}


def _to_int(value: str | None, default: int = 0) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    return int(float(text))


def _to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def _redirect(path: str, message: str | None = None, error: str | None = None, **params: str) -> RedirectResponse:
    query: dict[str, str] = {}
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    for key, value in params.items():
        if value is not None:
            query[key] = str(value)

    url = f"{path}?{urlencode(query)}" if query else path
    return RedirectResponse(url=url, status_code=303)


def _base_context(request: Request, active_nav: str, **extra):
    context = {
        "request": request,
        "active_nav": active_nav,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
        "reason_labels": REASON_LABELS,
    }
    context.update(extra)
    return context


def _load_plan(db: Session, order_id: str) -> PackingPlan | None:
    return db.scalar(
        select(PackingPlan)
        .options(
            selectinload(PackingPlan.shipments)
            .selectinload(PackingShipment.items)
            .selectinload(PackingShipmentItem.sku)
        )
        .where(PackingPlan.order_id == order_id)
        .order_by(PackingPlan.created_at.desc(), PackingPlan.id.desc())
        .limit(1)
    )


def _safe_ratio(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def _shipment_tags(shipment: PackingShipment) -> list[str]:
    tags = []
    for item in shipment.items:
        sku = item.sku
        if not sku:
            continue
        if sku.fragile:
            tags.append("fragile")
        if sku.hazmat:
            tags.append("hazmat")
        if sku.category in {"battery", "liquid"}:
            tags.append(sku.category)
    return sorted(set(tags))


def _generate_sku_id(name: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")
    cleaned = cleaned[:20] if cleaned else "AUTO"
    return f"SKU-{cleaned}-{datetime.utcnow().strftime('%H%M%S%f')}"


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        try:
            seed_if_empty(db)
            order_ids = db.scalars(select(Order.order_id)).all()
            for order_id in order_ids:
                if latest_plan(db, order_id) is None:
                    recalculate_order_plan(db, order_id)
        except Exception:
            db.rollback()


@app.exception_handler(Exception)
async def app_exception_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        "error.html",
        _base_context(request, active_nav="", error=str(exc)),
        status_code=500,
    )


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    unshipped_count = db.scalar(select(func.count()).select_from(Order).where(Order.status != "shipped")) or 0

    last_7d = datetime.utcnow() - timedelta(days=7)
    log_total_7d = db.scalar(
        select(func.count()).select_from(PackingExecutionLog).where(PackingExecutionLog.created_at >= last_7d)
    ) or 0
    log_match_7d = db.scalar(
        select(func.count())
        .select_from(PackingExecutionLog)
        .where(PackingExecutionLog.created_at >= last_7d, PackingExecutionLog.is_match.is_(True))
    ) or 0
    adoption_rate = (log_match_7d / log_total_7d * 100) if log_total_7d else 0.0

    plans = db.scalars(select(PackingPlan).options(selectinload(PackingPlan.shipments))).all()
    split_count = sum(1 for plan in plans if len(plan.shipments) > 1)
    estimate_total = sum((shipment.total_cost_yen or 0) for plan in plans for shipment in plan.shipments)

    top_reasons = db.execute(
        select(PackingExecutionLog.reason_code, func.count().label("cnt"))
        .where(PackingExecutionLog.reason_code.is_not(None), PackingExecutionLog.created_at >= last_7d)
        .group_by(PackingExecutionLog.reason_code)
        .order_by(func.count().desc())
        .limit(5)
    ).all()

    return templates.TemplateResponse(
        "dashboard.html",
        _base_context(
            request,
            active_nav="dashboard",
            unshipped_count=unshipped_count,
            adoption_rate=adoption_rate,
            split_count=split_count,
            estimate_total=estimate_total,
            top_reasons=top_reasons,
            safe_ratio=_safe_ratio,
        ),
    )


@app.get("/orders")
def orders(
    request: Request,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    channel: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(Order)

    from_date = _parse_date(date_from)
    to_date = _parse_date(date_to)

    if status and status != "all":
        query = query.where(Order.status == status)
    if channel and channel != "all":
        query = query.where(Order.channel == channel)
    if from_date:
        query = query.where(Order.order_date >= from_date)
    if to_date:
        query = query.where(Order.order_date <= to_date)

    query = query.order_by(Order.order_date.desc(), Order.order_id.asc())
    rows = db.scalars(query).all()

    for order in rows:
        if latest_plan(db, order.order_id) is None:
            try:
                recalculate_order_plan(db, order.order_id)
            except Exception:
                db.rollback()

    order_ids = [order.order_id for order in rows]
    plans = []
    if order_ids:
        plans = db.scalars(
            select(PackingPlan)
            .options(selectinload(PackingPlan.shipments))
            .where(PackingPlan.order_id.in_(order_ids))
        ).all()
    plan_by_order = {plan.order_id: plan for plan in plans}

    summaries = []
    for order in rows:
        plan = plan_by_order.get(order.order_id)
        shipments = sorted(plan.shipments, key=lambda s: s.shipment_no) if plan else []
        shipment_count = len(shipments)
        representative = shipments[0].recommended_box_id if shipments else "-"
        estimate_total = sum((shipment.total_cost_yen or 0) for shipment in shipments)
        summaries.append(
            {
                "order": order,
                "shipment_count": shipment_count,
                "representative_box": representative or "未提案",
                "estimate_total": estimate_total,
            }
        )

    channels = db.scalars(select(Order.channel).distinct().order_by(Order.channel.asc())).all()

    return templates.TemplateResponse(
        "orders.html",
        _base_context(
            request,
            active_nav="orders",
            summaries=summaries,
            filters={
                "status": status or "all",
                "channel": channel or "all",
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
            channels=channels,
        ),
    )


@app.post("/orders/{order_id}/recalculate")
def recalculate_order(order_id: str, request: Request, db: Session = Depends(get_db)):
    referer = request.headers.get("referer") or f"/orders/{order_id}"
    path = referer if referer.startswith("/") else f"/orders/{order_id}"
    try:
        recalculate_order_plan(db, order_id)
        return _redirect(path, message=f"{order_id} の提案を再計算しました")
    except Exception as exc:
        db.rollback()
        return _redirect(path, error=f"再計算に失敗しました: {exc}")


@app.get("/orders/{order_id}")
def order_detail(order_id: str, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    if latest_plan(db, order_id) is None:
        recalculate_order_plan(db, order_id)

    plan = _load_plan(db, order_id)
    items = db.scalars(
        select(OrderItem).options(selectinload(OrderItem.sku)).where(OrderItem.order_id == order_id).order_by(OrderItem.id.asc())
    ).all()

    shipments = sorted(plan.shipments, key=lambda s: s.shipment_no) if plan else []

    return templates.TemplateResponse(
        "order_detail.html",
        _base_context(
            request,
            active_nav="orders",
            order=order,
            items=items,
            shipments=shipments,
            shipment_tags=_shipment_tags,
            safe_ratio=_safe_ratio,
        ),
    )


@app.get("/packing")
def packing_search(request: Request):
    order_id = request.query_params.get("order_id")
    if order_id:
        return _redirect(f"/packing/{order_id}")
    return templates.TemplateResponse("packing.html", _base_context(request, active_nav="packing", order=None, shipments=[]))


@app.get("/packing/{order_id}")
def packing_assistant(order_id: str, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        return templates.TemplateResponse(
            "packing.html",
            _base_context(request, active_nav="packing", order=None, shipments=[], error="指定の注文が見つかりません"),
        )

    ensure_order_plan(db, order_id)
    plan = _load_plan(db, order_id)
    shipments = sorted(plan.shipments, key=lambda s: s.shipment_no) if plan else []
    boxes = db.scalars(select(Box).order_by(Box.box_id.asc())).all()

    latest_logs = db.scalars(
        select(PackingExecutionLog)
        .where(PackingExecutionLog.order_id == order_id)
        .order_by(PackingExecutionLog.created_at.desc())
    ).all()
    latest_by_no: dict[int, PackingExecutionLog] = {}
    for log in latest_logs:
        if log.shipment_no not in latest_by_no:
            latest_by_no[log.shipment_no] = log

    active = _to_int(request.query_params.get("active"), 1)

    return templates.TemplateResponse(
        "packing.html",
        _base_context(
            request,
            active_nav="packing",
            order=order,
            shipments=shipments,
            boxes=boxes,
            shipment_tags=_shipment_tags,
            safe_ratio=_safe_ratio,
            reason_options=REASON_LABELS,
            latest_by_no=latest_by_no,
            active_shipment=active,
        ),
    )


@app.post("/packing/{order_id}/shipments/{shipment_no}/confirm")
async def confirm_packing(order_id: str, shipment_no: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    actual_box_id = str(form.get("actual_box_id") or "").strip()
    reason_code = str(form.get("reason_code") or "").strip() or None
    reason_note = str(form.get("reason_note") or "").strip() or None
    worker_name = str(form.get("worker_name") or "").strip() or None

    if not actual_box_id:
        return _redirect(f"/packing/{order_id}", error="実際に使った箱を選択してください", active=str(shipment_no))

    plan = ensure_order_plan(db, order_id)
    shipment = db.scalar(
        select(PackingShipment)
        .where(PackingShipment.plan_id == plan.id, PackingShipment.shipment_no == shipment_no)
        .limit(1)
    )
    if not shipment:
        return _redirect(f"/packing/{order_id}", error="Shipmentが見つかりません", active=str(shipment_no))

    is_match = shipment.recommended_box_id == actual_box_id
    if not is_match and not reason_code:
        return _redirect(f"/packing/{order_id}", error="推奨と異なる場合は理由を選択してください", active=str(shipment_no))

    sku_ids = db.scalars(select(PackingShipmentItem.sku_id).where(PackingShipmentItem.shipment_id == shipment.id)).all()

    db.add(
        PackingExecutionLog(
            order_id=order_id,
            shipment_no=shipment_no,
            recommended_box_id=shipment.recommended_box_id,
            actual_box_id=actual_box_id,
            reason_code=reason_code,
            reason_note=reason_note,
            worker_name=worker_name,
            is_match=is_match,
            item_skus=",".join(sorted(set(sku_ids))),
        )
    )

    order = db.get(Order, order_id)
    if order and order.status in {"created", "picking"}:
        order.status = "packing"

    db.commit()
    return _redirect(f"/packing/{order_id}", message="梱包実績を保存しました", active=str(shipment_no))


@app.get("/simulator")
def simulator(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "simulator.html",
        _base_context(
            request,
            active_nav="simulator",
            form_data={
                "name": "",
                "category": "other",
                "length_mm": 200,
                "width_mm": 150,
                "height_mm": 50,
                "weight_g": 300,
                "padding_mm": 5,
                "can_rotate": True,
                "fragile": False,
                "carrier": "CarrierB",
            },
            candidates=[],
        ),
    )


@app.post("/simulator")
async def run_simulator(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        form_data = {
            "name": str(form.get("name") or "").strip(),
            "category": str(form.get("category") or "other").strip() or "other",
            "length_mm": _to_int(form.get("length_mm"), 0),
            "width_mm": _to_int(form.get("width_mm"), 0),
            "height_mm": _to_int(form.get("height_mm"), 0),
            "weight_g": _to_int(form.get("weight_g"), 0),
            "padding_mm": _to_int(form.get("padding_mm"), 0),
            "can_rotate": _to_bool(form.get("can_rotate")),
            "fragile": _to_bool(form.get("fragile")),
            "carrier": str(form.get("carrier") or "CarrierB"),
        }

        if min(form_data["length_mm"], form_data["width_mm"], form_data["height_mm"], form_data["weight_g"]) <= 0:
            raise ValueError("寸法と重量は0より大きい値を入力してください")

        item = build_virtual_item(
            name=form_data["name"],
            category=form_data["category"],
            length_mm=form_data["length_mm"],
            width_mm=form_data["width_mm"],
            height_mm=form_data["height_mm"],
            weight_g=form_data["weight_g"],
            padding_mm=form_data["padding_mm"],
            can_rotate=form_data["can_rotate"],
            fragile=form_data["fragile"],
        )
        boxes = db.scalars(select(Box)).all()
        rates = db.scalars(select(ShippingRate)).all()
        candidates = simulate_top_candidates(item, boxes, rates, carrier_preference=form_data["carrier"])

        return templates.TemplateResponse(
            "simulator.html",
            _base_context(
                request,
                active_nav="simulator",
                form_data=form_data,
                candidates=candidates,
                safe_ratio=_safe_ratio,
            ),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "simulator.html",
            _base_context(
                request,
                active_nav="simulator",
                form_data={
                    "name": str(form.get("name") or "").strip(),
                    "category": str(form.get("category") or "other").strip() or "other",
                    "length_mm": form.get("length_mm") or "",
                    "width_mm": form.get("width_mm") or "",
                    "height_mm": form.get("height_mm") or "",
                    "weight_g": form.get("weight_g") or "",
                    "padding_mm": form.get("padding_mm") or "",
                    "can_rotate": _to_bool(form.get("can_rotate")),
                    "fragile": _to_bool(form.get("fragile")),
                    "carrier": str(form.get("carrier") or "CarrierB"),
                },
                candidates=[],
                error=f"シミュレーションに失敗しました: {exc}",
            ),
        )


@app.post("/simulator/register_sku")
async def simulator_register_sku(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        name = str(form.get("name") or "").strip() or "シミュレーションSKU"
        sku_id = str(form.get("sku_id") or "").strip() or _generate_sku_id(name)
        while db.get(SKU, sku_id):
            sku_id = _generate_sku_id(name)

        sku = SKU(
            sku_id=sku_id,
            name=name,
            category=str(form.get("category") or "other").strip() or "other",
            length_mm=_to_int(form.get("length_mm"), 0),
            width_mm=_to_int(form.get("width_mm"), 0),
            height_mm=_to_int(form.get("height_mm"), 0),
            weight_g=_to_int(form.get("weight_g"), 0),
            padding_mm=_to_int(form.get("padding_mm"), 0),
            can_rotate=_to_bool(form.get("can_rotate")),
            fragile=_to_bool(form.get("fragile")),
            compressible=False,
            hazmat=False,
            prohibited_group=None,
        )
        db.add(sku)
        db.commit()
        return _redirect("/masters/skus", message=f"{sku_id} を登録しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/simulator", error=f"SKU登録に失敗しました: {exc}")


@app.get("/masters")
def masters_root():
    return RedirectResponse(url="/masters/skus", status_code=303)


@app.get("/masters/skus")
def masters_skus(request: Request, db: Session = Depends(get_db)):
    skus = db.scalars(select(SKU).order_by(SKU.sku_id.asc())).all()
    return templates.TemplateResponse(
        "masters_skus.html",
        _base_context(request, active_nav="masters", skus=skus, masters_tab="skus"),
    )


@app.get("/masters/skus/{sku_id}/edit")
def edit_sku_page(sku_id: str, request: Request, db: Session = Depends(get_db)):
    sku = db.get(SKU, sku_id)
    if not sku:
        return _redirect("/masters/skus", error="SKUが見つかりません")
    return templates.TemplateResponse(
        "sku_edit.html",
        _base_context(request, active_nav="masters", masters_tab="skus", sku=sku),
    )


@app.post("/masters/skus/create")
async def create_sku(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        sku_id = str(form.get("sku_id") or "").strip()
        if not sku_id:
            raise ValueError("sku_id は必須です")
        if db.get(SKU, sku_id):
            raise ValueError("同じ sku_id が既に存在します")

        db.add(
            SKU(
                sku_id=sku_id,
                name=str(form.get("name") or sku_id).strip(),
                category=str(form.get("category") or "other").strip() or "other",
                length_mm=_to_int(form.get("length_mm"), 0),
                width_mm=_to_int(form.get("width_mm"), 0),
                height_mm=_to_int(form.get("height_mm"), 0),
                weight_g=_to_int(form.get("weight_g"), 0),
                can_rotate=_to_bool(form.get("can_rotate")),
                fragile=_to_bool(form.get("fragile")),
                compressible=_to_bool(form.get("compressible")),
                hazmat=_to_bool(form.get("hazmat")),
                padding_mm=_to_int(form.get("padding_mm"), 0),
                prohibited_group=str(form.get("prohibited_group") or "").strip() or None,
            )
        )
        db.commit()
        return _redirect("/masters/skus", message=f"{sku_id} を追加しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/skus", error=f"SKU追加に失敗しました: {exc}")


@app.post("/masters/skus/{sku_id}/edit")
async def edit_sku(sku_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    sku = db.get(SKU, sku_id)
    if not sku:
        return _redirect("/masters/skus", error="SKUが見つかりません")

    try:
        sku.name = str(form.get("name") or sku.name).strip() or sku.name
        sku.category = str(form.get("category") or sku.category).strip() or sku.category
        sku.length_mm = _to_int(form.get("length_mm"), sku.length_mm)
        sku.width_mm = _to_int(form.get("width_mm"), sku.width_mm)
        sku.height_mm = _to_int(form.get("height_mm"), sku.height_mm)
        sku.weight_g = _to_int(form.get("weight_g"), sku.weight_g)
        sku.can_rotate = _to_bool(form.get("can_rotate"))
        sku.fragile = _to_bool(form.get("fragile"))
        sku.compressible = _to_bool(form.get("compressible"))
        sku.hazmat = _to_bool(form.get("hazmat"))
        sku.padding_mm = _to_int(form.get("padding_mm"), sku.padding_mm)
        sku.prohibited_group = str(form.get("prohibited_group") or "").strip() or None
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/skus", message=f"{sku_id} を更新しました")
    except Exception as exc:
        db.rollback()
        return _redirect(f"/masters/skus/{sku_id}/edit", error=f"SKU更新に失敗しました: {exc}")


@app.post("/masters/skus/import")
async def import_skus(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        rows = read_csv_rows_from_bytes(await file.read())
        count = upsert_skus(db, rows)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/skus", message=f"SKUを{count}件インポートしました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/skus", error=f"SKUインポートに失敗しました: {exc}")


@app.get("/masters/skus/export")
def export_skus(db: Session = Depends(get_db)):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
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
        ]
    )
    skus = db.scalars(select(SKU).order_by(SKU.sku_id.asc())).all()
    for sku in skus:
        writer.writerow(
            [
                sku.sku_id,
                sku.name,
                sku.category,
                sku.length_mm,
                sku.width_mm,
                sku.height_mm,
                sku.weight_g,
                int(sku.can_rotate),
                int(sku.fragile),
                int(sku.compressible),
                int(sku.hazmat),
                sku.padding_mm,
                sku.prohibited_group or "",
            ]
        )

    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=skus_export.csv"},
    )


@app.get("/masters/boxes")
def masters_boxes(request: Request, db: Session = Depends(get_db)):
    boxes = db.scalars(select(Box).order_by(Box.box_id.asc())).all()
    return templates.TemplateResponse(
        "masters_boxes.html",
        _base_context(request, active_nav="masters", boxes=boxes, masters_tab="boxes"),
    )


@app.get("/masters/boxes/{box_id}/edit")
def edit_box_page(box_id: str, request: Request, db: Session = Depends(get_db)):
    box = db.get(Box, box_id)
    if not box:
        return _redirect("/masters/boxes", error="箱が見つかりません")
    return templates.TemplateResponse(
        "box_edit.html",
        _base_context(request, active_nav="masters", masters_tab="boxes", box=box),
    )


@app.post("/masters/boxes/create")
async def create_box(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        box_id = str(form.get("box_id") or "").strip()
        if not box_id:
            raise ValueError("box_id は必須です")
        if db.get(Box, box_id):
            raise ValueError("同じ box_id が既に存在します")

        db.add(
            Box(
                box_id=box_id,
                name=str(form.get("name") or box_id).strip(),
                inner_length_mm=_to_int(form.get("inner_length_mm"), 0),
                inner_width_mm=_to_int(form.get("inner_width_mm"), 0),
                inner_height_mm=_to_int(form.get("inner_height_mm"), 0),
                max_weight_g=_to_int(form.get("max_weight_g"), 0),
                box_cost_yen=_to_int(form.get("box_cost_yen"), 0),
                box_type=str(form.get("box_type") or "box").strip() or "box",
                outer_length_mm=_to_int(form.get("outer_length_mm"), 0),
                outer_width_mm=_to_int(form.get("outer_width_mm"), 0),
                outer_height_mm=_to_int(form.get("outer_height_mm"), 0),
            )
        )
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/boxes", message=f"{box_id} を追加しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/boxes", error=f"箱追加に失敗しました: {exc}")


@app.post("/masters/boxes/{box_id}/edit")
async def edit_box(box_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    box = db.get(Box, box_id)
    if not box:
        return _redirect("/masters/boxes", error="箱が見つかりません")

    try:
        box.name = str(form.get("name") or box.name).strip() or box.name
        box.inner_length_mm = _to_int(form.get("inner_length_mm"), box.inner_length_mm)
        box.inner_width_mm = _to_int(form.get("inner_width_mm"), box.inner_width_mm)
        box.inner_height_mm = _to_int(form.get("inner_height_mm"), box.inner_height_mm)
        box.max_weight_g = _to_int(form.get("max_weight_g"), box.max_weight_g)
        box.box_cost_yen = _to_int(form.get("box_cost_yen"), box.box_cost_yen)
        box.box_type = str(form.get("box_type") or box.box_type).strip() or box.box_type
        box.outer_length_mm = _to_int(form.get("outer_length_mm"), box.outer_length_mm)
        box.outer_width_mm = _to_int(form.get("outer_width_mm"), box.outer_width_mm)
        box.outer_height_mm = _to_int(form.get("outer_height_mm"), box.outer_height_mm)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/boxes", message=f"{box_id} を更新しました")
    except Exception as exc:
        db.rollback()
        return _redirect(f"/masters/boxes/{box_id}/edit", error=f"箱更新に失敗しました: {exc}")


@app.post("/masters/boxes/import")
async def import_boxes(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        rows = read_csv_rows_from_bytes(await file.read())
        count = upsert_boxes(db, rows)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/boxes", message=f"箱を{count}件インポートしました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/boxes", error=f"箱インポートに失敗しました: {exc}")


@app.get("/masters/rates")
def masters_rates(request: Request, db: Session = Depends(get_db)):
    rates = db.scalars(select(ShippingRate).order_by(ShippingRate.carrier.asc(), ShippingRate.service.asc())).all()
    return templates.TemplateResponse(
        "masters_rates.html",
        _base_context(request, active_nav="masters", rates=rates, masters_tab="rates"),
    )


@app.get("/masters/rates/{rate_id}/edit")
def edit_rate_page(rate_id: int, request: Request, db: Session = Depends(get_db)):
    rate = db.get(ShippingRate, rate_id)
    if not rate:
        return _redirect("/masters/rates", error="運賃が見つかりません")
    return templates.TemplateResponse(
        "rate_edit.html",
        _base_context(request, active_nav="masters", masters_tab="rates", rate=rate),
    )


@app.post("/masters/rates/create")
async def create_rate(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        db.add(
            ShippingRate(
                carrier=str(form.get("carrier") or "").strip(),
                service=str(form.get("service") or "").strip(),
                size_class=str(form.get("size_class") or "").strip(),
                max_weight_g=_to_int(form.get("max_weight_g"), 0),
                price_yen=_to_int(form.get("price_yen"), 0),
            )
        )
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/rates", message="運賃を追加しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/rates", error=f"運賃追加に失敗しました: {exc}")


@app.post("/masters/rates/{rate_id}/edit")
async def edit_rate(rate_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    rate = db.get(ShippingRate, rate_id)
    if not rate:
        return _redirect("/masters/rates", error="運賃が見つかりません")

    try:
        rate.carrier = str(form.get("carrier") or rate.carrier).strip() or rate.carrier
        rate.service = str(form.get("service") or rate.service).strip() or rate.service
        rate.size_class = str(form.get("size_class") or rate.size_class).strip() or rate.size_class
        rate.max_weight_g = _to_int(form.get("max_weight_g"), rate.max_weight_g)
        rate.price_yen = _to_int(form.get("price_yen"), rate.price_yen)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/rates", message="運賃を更新しました")
    except Exception as exc:
        db.rollback()
        return _redirect(f"/masters/rates/{rate_id}/edit", error=f"運賃更新に失敗しました: {exc}")


@app.post("/masters/rates/import")
async def import_rates(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        rows = read_csv_rows_from_bytes(await file.read())
        count = replace_shipping_rates(db, rows)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/rates", message=f"運賃を{count}件インポートしました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/rates", error=f"運賃インポートに失敗しました: {exc}")


@app.get("/masters/prohibited")
def masters_prohibited(request: Request, db: Session = Depends(get_db)):
    rules = db.scalars(select(ProhibitedGroupPair).order_by(ProhibitedGroupPair.group_a.asc())).all()
    return templates.TemplateResponse(
        "masters_prohibited.html",
        _base_context(request, active_nav="masters", rules=rules, masters_tab="prohibited"),
    )


@app.post("/masters/prohibited/create")
async def create_prohibited(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        a = str(form.get("group_a") or "").strip()
        b = str(form.get("group_b") or "").strip()
        reason = str(form.get("reason") or "同梱不可").strip()
        if not a or not b:
            raise ValueError("group_a / group_b は必須です")
        db.add(ProhibitedGroupPair(group_a=a, group_b=b, reason=reason))
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/prohibited", message="同梱禁止ルールを追加しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/prohibited", error=f"同梱禁止ルールの追加に失敗しました: {exc}")


@app.post("/masters/prohibited/import")
async def import_prohibited(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        rows = read_csv_rows_from_bytes(await file.read())
        count = replace_prohibited_pairs(db, rows)
        db.commit()
        recalculate_all_orders(db)
        return _redirect("/masters/prohibited", message=f"同梱禁止ルールを{count}件インポートしました")
    except Exception as exc:
        db.rollback()
        return _redirect("/masters/prohibited", error=f"同梱禁止ルールのインポートに失敗しました: {exc}")


@app.get("/logs")
def logs(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    reason: str | None = None,
    sku: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(PackingExecutionLog)

    from_date = _parse_date(date_from)
    to_date = _parse_date(date_to)

    conditions = []
    if from_date:
        conditions.append(PackingExecutionLog.created_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        conditions.append(PackingExecutionLog.created_at <= datetime.combine(to_date, datetime.max.time()))
    if reason and reason != "all":
        if reason == "MATCH":
            conditions.append(PackingExecutionLog.is_match.is_(True))
        else:
            conditions.append(PackingExecutionLog.reason_code == reason)
    if sku:
        conditions.append(PackingExecutionLog.item_skus.like(f"%{sku}%"))

    if conditions:
        query = query.where(and_(*conditions))

    records = db.scalars(query.order_by(PackingExecutionLog.created_at.desc()).limit(1000)).all()

    total = len(records)
    matched = sum(1 for row in records if row.is_match)
    adoption_rate = (matched / total * 100) if total else 0.0

    reason_counter = Counter(row.reason_code for row in records if row.reason_code)
    top_reasons = reason_counter.most_common(5)

    no_fit_counter: Counter[str] = Counter()
    for row in records:
        if row.reason_code != "NO_FIT" or not row.item_skus:
            continue
        for sku_id in [x.strip() for x in row.item_skus.split(",") if x.strip()]:
            no_fit_counter[sku_id] += 1

    return templates.TemplateResponse(
        "logs.html",
        _base_context(
            request,
            active_nav="logs",
            records=records,
            adoption_rate=adoption_rate,
            top_reasons=top_reasons,
            no_fit_skus=no_fit_counter.most_common(5),
            filters={
                "date_from": date_from or "",
                "date_to": date_to or "",
                "reason": reason or "all",
                "sku": sku or "",
            },
            reason_options=REASON_LABELS,
        ),
    )


@app.post("/admin/reset")
def admin_reset(db: Session = Depends(get_db)):
    try:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        seed_if_empty(db, force=True)
        recalculate_all_orders(db)
        return _redirect("/", message="DBを初期化しseedデータを再投入しました")
    except Exception as exc:
        db.rollback()
        return _redirect("/", error=f"初期化に失敗しました: {exc}")

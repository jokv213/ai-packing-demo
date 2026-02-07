from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SKU(Base):
    __tablename__ = "skus"

    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    can_rotate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fragile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    compressible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hazmat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    padding_mm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prohibited_group: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Box(Base):
    __tablename__ = "boxes"

    box_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    inner_length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    inner_width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    inner_height_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    max_weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    box_cost_yen: Mapped[int] = mapped_column(Integer, nullable=False)
    box_type: Mapped[str] = mapped_column(String(32), nullable=False, default="box")
    outer_length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    outer_width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    outer_height_mm: Mapped[int] = mapped_column(Integer, nullable=False)


class ShippingRate(Base):
    __tablename__ = "shipping_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    carrier: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    size_class: Mapped[str] = mapped_column(String(16), nullable=False)
    max_weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    price_yen: Mapped[int] = mapped_column(Integer, nullable=False)


class ProhibitedGroupPair(Base):
    __tablename__ = "prohibited_group_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_a: Mapped[str] = mapped_column(String(64), nullable=False)
    group_b: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_prefecture: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[OrderItem]] = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    plans: Mapped[list[PackingPlan]] = relationship("PackingPlan", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.sku_id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship("Order", back_populates="items")
    sku: Mapped[SKU] = relationship("SKU")


class PackingPlan(Base):
    __tablename__ = "packing_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    order: Mapped[Order] = relationship("Order", back_populates="plans")
    shipments: Mapped[list[PackingShipment]] = relationship(
        "PackingShipment", back_populates="plan", cascade="all, delete-orphan"
    )


class PackingShipment(Base):
    __tablename__ = "packing_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("packing_plans.id"), nullable=False, index=True)
    shipment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    split_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    recommended_box_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommended_box_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    shipping_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    box_cost_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fill_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    plan: Mapped[PackingPlan] = relationship("PackingPlan", back_populates="shipments")
    items: Mapped[list[PackingShipmentItem]] = relationship(
        "PackingShipmentItem", back_populates="shipment", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("plan_id", "shipment_no", name="uq_shipment_plan_no"),)


class PackingShipmentItem(Base):
    __tablename__ = "packing_shipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("packing_shipments.id"), nullable=False, index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.sku_id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    shipment: Mapped[PackingShipment] = relationship("PackingShipment", back_populates="items")
    sku: Mapped[SKU] = relationship("SKU")


class PackingExecutionLog(Base):
    __tablename__ = "packing_execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    shipment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_box_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_box_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    item_skus: Mapped[str | None] = mapped_column(Text, nullable=True)

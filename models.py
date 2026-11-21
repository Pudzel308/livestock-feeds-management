from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def is_active(self):
        return self.enabled

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.String(255))
    timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship("User")


class StockLot(db.Model):
    __tablename__ = "stock_lots"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("feed_products.id"),
        nullable=False,
    )
    quantity = db.Column(db.Float, nullable=False, default=0)
    condition = db.Column(db.String(30), nullable=False, default="saleable")
    expiry_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    product = db.relationship("FeedProduct", backref="lots")


class StockConditionLog(db.Model):
    __tablename__ = "stock_condition_logs"

    id = db.Column(db.Integer, primary_key=True)
    lot_id = db.Column(
        db.Integer,
        db.ForeignKey("stock_lots.id"),
        nullable=False,
    )
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("feed_products.id"),
        nullable=False,
    )
    previous_condition = db.Column(db.String(30), nullable=False)
    new_condition = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    remark = db.Column(db.String(500), nullable=False)
    changed_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    lot = db.relationship("StockLot")
    product = db.relationship("FeedProduct")
    changed_by = db.relationship("User")


class FeedProduct(db.Model):
    __tablename__ = "feed_products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    category = db.Column(db.String(80), nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    stock_quantity = db.Column(db.Float, nullable=False, default=0)
    unit = db.Column(db.String(20), nullable=False, default="kg")
    low_stock_threshold = db.Column(
        db.Float, nullable=False, default=10
    )

    inventory_logs = db.relationship(
        "InventoryLog",
        backref="product",
        lazy=True
    )

class InventoryLog(db.Model):
    __tablename__ = "inventory_logs"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("feed_products.id"),
        nullable=False
    )
    action_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    remarks = db.Column(db.String(255))

class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    timestamp = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    items = db.relationship(
        "SaleItem",
        backref="sale",
        cascade="all, delete-orphan",
        lazy=True,
    )

class SaleItem(db.Model):
    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id"),
        nullable=False,
    )
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("feed_products.id"),
        nullable=False,
    )
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)

    product = db.relationship("FeedProduct")

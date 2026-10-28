from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

import pandas as pd

from models import db, FeedProduct, InventoryLog, Sale, SaleItem


app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///livestock.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "susi-ng-mga-it"

db.init_app(app)

@app.route("/dashboard")
def dashboard():
    total_sales = db.session.scalar(
        select(func.count(Sale.id))
    ) or 0

    total_revenue = db.session.scalar(
        select(func.coalesce(func.sum(Sale.total_amount), 0))
    )

    low_stock_count = db.session.scalar(
        select(func.count(FeedProduct.id)).where(
            FeedProduct.stock_quantity > 0,
            FeedProduct.stock_quantity <= FeedProduct.low_stock_threshold,
        )
    ) or 0

    out_of_stock_count = db.session.scalar(
        select(func.count(FeedProduct.id)).where(
            FeedProduct.stock_quantity <= 0
        )
    ) or 0

    recent_sales = db.session.execute(
        select(Sale)
        .order_by(Sale.timestamp.desc(), Sale.id.desc())
        .limit(5)
    ).scalars().all()

    return render_template(
        "dashboard.html",
        total_sales=total_sales,
        total_revenue=total_revenue,
        low_stock_count=low_stock_count,
        out_of_stock_count=out_of_stock_count,
        recent_sales=recent_sales,
    )

@app.route("/")
def index():
    products = db.session.execute(
        select(FeedProduct).order_by(FeedProduct.name)
    ).scalars().all()

    return render_template("index.html", products=products)


@app.route("/products/<int:product_id>/add-stock", methods=["POST"])
def add_stock(product_id):
    quantity = request.form.get("quantity", type=float)
    remarks = request.form.get("remarks", "").strip()

    if quantity is None or quantity <= 0:
        flash("Enter a quantity greater than zero.", "error")
        return redirect(url_for("index"))

    try:
        product = db.session.get(FeedProduct, product_id)

        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("index"))

        product.stock_quantity += quantity

        db.session.add(
            InventoryLog(
                product_id=product.id,
                action_type="stock_added",
                quantity=quantity,
                remarks=remarks or "Stock replenishment",
            )
        )

        db.session.commit()
        flash(f"Added {quantity:g} {product.unit} to {product.name}.", "success")

    except SQLAlchemyError:
        db.session.rollback()
        flash("Error updating stock.", "error")

    return redirect(url_for("index"))


@app.route("/sales", methods=["GET", "POST"])
def sales():
    products = db.session.execute(
        select(FeedProduct).order_by(FeedProduct.name)
    ).scalars().all()

    if request.method == "GET":
        return render_template("sales.html", products=products)

    requested_items = []

    # Validate every requested item before changing inventory.
    for product in products:
        raw_quantity = request.form.get(f"quantity_{product.id}", "").strip()

        if not raw_quantity:
            continue

        try:
            quantity_decimal = Decimal(raw_quantity)
        except InvalidOperation:
            flash(f"Invalid quantity for {product.name}.", "error")
            return redirect(url_for("sales"))

        if (
            not quantity_decimal.is_finite()
            or quantity_decimal <= 0
        ):
            flash(
                f"Quantity for {product.name} must be greater than zero.",
                "error",
            )
            return redirect(url_for("sales"))

        if quantity_decimal.as_tuple().exponent < -2:
            flash(
                f"Quantity for {product.name} can have at most "
                "two decimal places.",
                "error",
            )
            return redirect(url_for("sales"))

        quantity = float(quantity_decimal)

        if quantity > product.stock_quantity:
            flash(
                f"Insufficient stock for {product.name}. "
                f"Available: {product.stock_quantity:g} {product.unit}.",
                "error",
            )
            return redirect(url_for("sales"))

        unit_price = Decimal(str(product.unit_price))
        subtotal = (unit_price * quantity_decimal).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        requested_items.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "subtotal": subtotal,
            }
        )

    if not requested_items:
        flash("Select at least one product and enter a quantity.", "error")
        return redirect(url_for("sales"))

    total = sum(
        (item["subtotal"] for item in requested_items),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    try:
        sale = Sale(total_amount=total)
        db.session.add(sale)
        db.session.flush()

        for item in requested_items:
            product = item["product"]
            quantity = item["quantity"]

            # Recheck the stock immediately before deducting it.
            db.session.refresh(product)

            if quantity > product.stock_quantity:
                raise ValueError(
                    f"Insufficient stock for {product.name}. "
                    "The transaction was cancelled."
                )

            product.stock_quantity -= quantity

            db.session.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=item["unit_price"],
                    subtotal=item["subtotal"],
                )
            )

            db.session.add(
                InventoryLog(
                    product_id=product.id,
                    action_type="stock_sold",
                    quantity=quantity,
                    remarks=f"Sale #{sale.id}",
                )
            )

        db.session.commit()

        flash(
            f"Sale #{sale.id} recorded successfully. Total: ₱{total:.2f}",
            "success",
        )
        return redirect(url_for("sale_detail", sale_id=sale.id))

    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    except SQLAlchemyError:
        db.session.rollback()
        flash("The sale could not be saved. No changes were committed.", "error")

    return redirect(url_for("sales"))


@app.route("/sales/history")
def sales_history():
    all_sales = db.session.execute(
        select(Sale).order_by(Sale.timestamp.desc(), Sale.id.desc())
    ).scalars().all()

    return render_template("sales_history.html", sales=all_sales)


@app.route("/sales/<int:sale_id>")
def sale_detail(sale_id):
    sale = db.session.get(Sale, sale_id)

    if sale is None:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_history"))

    return render_template("sale_detail.html", sale=sale)


with app.app_context():
    db.create_all()




def _build_sales_trend(sales_frame, frequency, label_format):
    """Return complete period buckets between the first and last sale."""
    if sales_frame.empty:
        return {"labels": [], "sales": [], "revenue": []}

    periods = sales_frame["timestamp"].dt.to_period(frequency)
    grouped = (
        sales_frame.assign(period=periods)
        .groupby("period")
        .agg(sales=("id", "count"), revenue=("total_amount", "sum"))
    )
    all_periods = pd.period_range(
        start=grouped.index.min(),
        end=grouped.index.max(),
        freq=frequency,
    )
    grouped = grouped.reindex(all_periods, fill_value=0)

    return {
        "labels": [period.start_time.strftime(label_format) for period in all_periods],
        "sales": [int(value) for value in grouped["sales"].tolist()],
        "revenue": [round(float(value), 2) for value in grouped["revenue"].tolist()],
    }


def _describe_values(label, values):
    """Create JSON- and template-friendly descriptive statistics."""
    series = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if series.empty:
        return {
            "label": label,
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
        }

    return {
        "label": label,
        "count": int(series.count()),
        "mean": round(float(series.mean()), 2),
        "median": round(float(series.median()), 2),
        "std": round(float(series.std(ddof=1)), 2) if len(series) > 1 else None,
        "min": round(float(series.min()), 2),
        "max": round(float(series.max()), 2),
    }


@app.route("/analytics")
def analytics():
    sales_rows = db.session.execute(
        select(Sale.id, Sale.timestamp, Sale.total_amount)
        .order_by(Sale.timestamp, Sale.id)
    ).all()
    sales_frame = pd.DataFrame([dict(row._mapping) for row in sales_rows])

    if not sales_frame.empty:
        sales_frame["timestamp"] = (
            pd.to_datetime(sales_frame["timestamp"], errors="coerce", utc=True)
            .dt.tz_convert(None)
        )
        sales_frame["total_amount"] = pd.to_numeric(
            sales_frame["total_amount"], errors="coerce"
        ).fillna(0.0)
        sales_frame = sales_frame.dropna(subset=["timestamp"])

    item_rows = db.session.execute(
        select(
            SaleItem.quantity,
            SaleItem.subtotal,
            FeedProduct.name.label("product_name"),
            FeedProduct.unit,
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .join(FeedProduct, SaleItem.product_id == FeedProduct.id)
    ).all()
    items_frame = pd.DataFrame([dict(row._mapping) for row in item_rows])

    if not items_frame.empty:
        items_frame["quantity"] = pd.to_numeric(
            items_frame["quantity"], errors="coerce"
        ).fillna(0.0)
        items_frame["subtotal"] = pd.to_numeric(
            items_frame["subtotal"], errors="coerce"
        ).fillna(0.0)

    top_sellers = []
    if not items_frame.empty:
        product_totals = (
            items_frame.groupby(["product_name", "unit"], as_index=False)
            .agg(units_sold=("quantity", "sum"), revenue=("subtotal", "sum"))
            .sort_values(["units_sold", "revenue"], ascending=[False, False])
            .head(5)
        )
        top_sellers = [
            {
                "name": str(row.product_name),
                "unit": str(row.unit),
                "units_sold": round(float(row.units_sold), 2),
                "revenue": round(float(row.revenue), 2),
            }
            for row in product_totals.itertuples(index=False)
        ]

    sales_trends = {
        "daily": _build_sales_trend(sales_frame, "D", "%Y-%m-%d"),
        "weekly": _build_sales_trend(sales_frame, "W-SUN", "%Y-%m-%d"),
        "monthly": _build_sales_trend(sales_frame, "M", "%b %Y"),
    }

    inventory_rows = db.session.execute(
        select(
            InventoryLog.timestamp,
            InventoryLog.action_type,
            InventoryLog.quantity,
            InventoryLog.remarks,
            FeedProduct.name.label("product_name"),
            FeedProduct.unit,
        )
        .join(FeedProduct, InventoryLog.product_id == FeedProduct.id)
    ).all()
    inventory_frame = pd.DataFrame([dict(row._mapping) for row in inventory_rows])

    inventory_movement = []
    recent_inventory_logs = []
    if not inventory_frame.empty:
        inventory_frame["timestamp"] = (
            pd.to_datetime(inventory_frame["timestamp"], errors="coerce", utc=True)
            .dt.tz_convert(None)
        )
        inventory_frame["quantity"] = pd.to_numeric(
            inventory_frame["quantity"], errors="coerce"
        ).fillna(0.0)
        tracked_actions = inventory_frame[
            inventory_frame["action_type"].isin(["stock_added", "stock_sold"])
        ]

        if not tracked_actions.empty:
            movement = (
                tracked_actions.pivot_table(
                    index=["product_name", "unit"],
                    columns="action_type",
                    values="quantity",
                    aggfunc="sum",
                    fill_value=0.0,
                )
                .reset_index()
            )
            for action in ("stock_added", "stock_sold"):
                if action not in movement:
                    movement[action] = 0.0
            movement["total_movement"] = (
                movement["stock_added"] + movement["stock_sold"]
            )
            movement = movement.sort_values("total_movement", ascending=False)
            inventory_movement = [
                {
                    "name": str(row.product_name),
                    "unit": str(row.unit),
                    "stock_added": round(float(row.stock_added), 2),
                    "stock_sold": round(float(row.stock_sold), 2),
                    "net_change": round(
                        float(row.stock_added - row.stock_sold), 2
                    ),
                }
                for row in movement.itertuples(index=False)
            ]

        latest_logs = inventory_frame.dropna(subset=["timestamp"]).sort_values(
            "timestamp", ascending=False
        ).head(10)
        action_labels = {
            "stock_added": "Stock added",
            "stock_sold": "Stock sold",
        }
        recent_inventory_logs = [
            {
                "timestamp": row.timestamp.strftime("%Y-%m-%d %H:%M UTC"),
                "product_name": str(row.product_name),
                "unit": str(row.unit),
                "action": action_labels.get(
                    str(row.action_type), str(row.action_type).replace("_", " ").title()
                ),
                "quantity": round(float(row.quantity), 2),
                "remarks": row.remarks or "—",
            }
            for row in latest_logs.itertuples(index=False)
        ]

    daily_revenue = sales_trends["daily"]["revenue"]
    descriptive_stats = []
    if not sales_frame.empty:
        descriptive_stats = [
            _describe_values("Sale amount (₱)", sales_frame["total_amount"]),
            _describe_values("Daily revenue (₱)", daily_revenue),
        ]

    total_revenue = float(sales_frame["total_amount"].sum()) if not sales_frame.empty else 0.0
    total_sales = int(len(sales_frame))

    return render_template(
        "analytics.html",
        total_sales=total_sales,
        total_revenue=round(total_revenue, 2),
        average_sale=round(total_revenue / total_sales, 2) if total_sales else 0.0,
        products_moved=len(inventory_movement),
        inventory_log_count=len(inventory_frame),
        sales_trends=sales_trends,
        top_sellers=top_sellers,
        inventory_movement=inventory_movement,
        recent_inventory_logs=recent_inventory_logs,
        descriptive_stats=descriptive_stats,
    )
if __name__ == "__main__":
    app.run(debug=True)

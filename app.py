from flask import (
    abort,
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
)
from models import (
    db, 
    FeedProduct, 
    InventoryLog, 
    Sale, 
    SaleItem, 
    User,
    ActivityLog,
    StockLot,
    StockConditionLog,
)
from flask_login import (
    LoginManager, 
    current_user, 
    login_required, 
    login_user, 
    logout_user
)
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from functools import wraps
from io import BytesIO
import pandas as pd

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///livestock.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "susi-ng-mga-it"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


CONDITIONS = {
    "saleable",
    "near_expiry",
    "damaged",
    "spoiled",
    "expired",
    "quarantined",
    "destroyed",
}

SELLABLE_CONDITIONS = {"saleable", "near_expiry"}

db.init_app(app)

def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def send_excel_workbook(output, filename):
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


@login_manager.user_loader
def load_user(user_id, remember=False):
    return db.session.get(User, int(user_id))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = db.session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()

        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return redirect(url_for("login"))

        if not user.enabled:
            flash("This account is disabled.", "error")
            return redirect(url_for("login"))

        login_user(user)

        try:
            record_activity("login", "User logged in")
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Could not save login activity")

        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    try:
        record_activity("logout", "User logged out")
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Could not save logout activity")

    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
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

    inventory_movement, recent_inventory_logs, inventory_log_count = (
        _get_inventory_data()
    )

    return render_template(
        "dashboard.html",
        total_sales=total_sales,
        total_revenue=total_revenue,
        low_stock_count=low_stock_count,
        out_of_stock_count=out_of_stock_count,
        recent_sales=recent_sales,
        inventory_movement=inventory_movement,
        recent_inventory_logs=recent_inventory_logs,
        products_moved=len(inventory_movement)
    )


@app.route("/dashboard/export")
@login_required
def export_dashboard():
    total_sales = db.session.scalar(
        select(func.count(Sale.id))
    ) or 0

    total_revenue = db.session.scalar(
        select(func.coalesce(func.sum(Sale.total_amount), 0))
    ) or 0

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

    inventory_movement, recent_inventory_logs, _ = _get_inventory_data()

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary = pd.DataFrame([
            {"Metric": "Total Sales", "Value": total_sales},
            {"Metric": "Total Revenue", "Value": float(total_revenue)},
            {"Metric": "Low-Stock Products", "Value": low_stock_count},
            {"Metric": "Out-of-Stock Products", "Value": out_of_stock_count},
            {"Metric": "Products with Logged Movement",
             "Value": len(inventory_movement)},
        ])
        summary.to_excel(writer, sheet_name="Summary", index=False)

        movement_columns = [
            "name", "unit", "stock_added", "stock_sold", "net_change"
        ]
        movement_frame = pd.DataFrame(
            inventory_movement,
            columns=movement_columns,
        ).rename(columns={
            "name": "Product",
            "unit": "Unit",
            "stock_added": "Stock Added",
            "stock_sold": "Stock Sold",
            "net_change": "Net Change",
        })
        movement_frame.to_excel(
            writer,
            sheet_name="Inventory Movement",
            index=False,
        )

        log_columns = [
            "timestamp", "product_name", "unit", "action", "quantity",
            "remarks",
        ]
        logs_frame = pd.DataFrame(
            recent_inventory_logs,
            columns=log_columns,
        ).rename(columns={
            "timestamp": "Date and Time",
            "product_name": "Product",
            "unit": "Unit",
            "action": "Action",
            "quantity": "Quantity",
            "remarks": "Remarks",
        })
        logs_frame.to_excel(
            writer,
            sheet_name="Recent Inventory Logs",
            index=False,
        )

        if current_user.role == "admin":
            sales = db.session.execute(
                select(Sale)
                .order_by(Sale.timestamp.desc(), Sale.id.desc())
                .limit(5)
            ).scalars().all()

            sales_frame = pd.DataFrame([
                {
                    "Sale ID": sale.id,
                    "Date and Time": sale.timestamp.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "Items": len(sale.items),
                    "Total Amount": float(sale.total_amount),
                }
                for sale in sales
            ], columns=[
                "Sale ID", "Date and Time", "Items", "Total Amount"
            ])
            sales_frame.to_excel(
                writer,
                sheet_name="Recent Sales",
                index=False,
            )

    return send_excel_workbook(output, "dashboard_report.xlsx")


@app.route("/")
@login_required
def index():
    selected_category = request.args.get("filter", "").strip()
    search = request.args.get("search", "").strip()

    query = select(FeedProduct)
    conditions = []

    if selected_category:
        conditions.append(FeedProduct.category == selected_category)

    if search:
        pattern = f"%{search}%"
        conditions.append(
            (FeedProduct.name.ilike(pattern))
            | (FeedProduct.category.ilike(pattern))
        )

    if conditions:
        query = query.where(*conditions)

    products = db.session.execute(
        query.order_by(FeedProduct.name)
    ).scalars().all()

    lots = db.session.execute(
        select(StockLot)
        .where(StockLot.quantity > 0)
        .order_by(
            StockLot.product_id,
            StockLot.expiry_date.asc().nulls_last(),
            StockLot.created_at.asc(),
        )
    ).scalars().all()

    product_suggestions = db.session.execute(
        select(FeedProduct).order_by(FeedProduct.name)
    ).scalars().all()

    return render_template(
        "index.html",
        products=products,
        lots=lots,
        product_suggestions=product_suggestions,
    )


@app.route("/lots/<int:lot_id>/condition", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def change_lot_condition(lot_id):
    lot = db.session.get(StockLot, lot_id)
    if lot is None:
        abort(404)

    new_condition = request.form.get("condition", "").strip()
    raw_quantity = request.form.get("quantity", "").strip()
    remark = request.form.get("remark", "").strip()

    if new_condition not in CONDITIONS:
        flash("Choose a valid stock condition.", "error")
        return redirect(url_for("index"))

    if not remark:
        flash("A description is required.", "error")
        return redirect(url_for("index"))

    try:
        quantity = float(raw_quantity)
    except (TypeError, ValueError):
        flash("Enter a valid quantity.", "error")
        return redirect(url_for("index"))

    if quantity <= 0 or quantity > lot.quantity:
        flash(
            "Quantity must be greater than zero and no larger than the lot quantity.",
            "error",
        )
        return redirect(url_for("index"))

    previous_condition = lot.condition
    is_full_lot = quantity >= lot.quantity - 1e-9
    log_lot = lot

    try:
        if new_condition == "destroyed":
            # Remove only the affected amount. Keep any remaining amount
            # in its original lot and condition.
            lot.quantity = max(0, lot.quantity - quantity)
            if lot.quantity == 0:
                lot.condition = "destroyed"

            lot.product.stock_quantity = max(
                0, lot.product.stock_quantity - quantity
            )

        elif is_full_lot:
            lot.condition = new_condition

        else:
            # Split the affected quantity into a new lot.
            lot.quantity -= quantity
            log_lot = StockLot(
                product_id=lot.product_id,
                quantity=quantity,
                condition=new_condition,
                expiry_date=lot.expiry_date,
            )
            db.session.add(log_lot)
            db.session.flush()

        db.session.add(
            StockConditionLog(
                lot_id=log_lot.id,
                product_id=lot.product_id,
                previous_condition=previous_condition,
                new_condition=new_condition,
                quantity=quantity,
                remark=remark,
                changed_by_id=current_user.id,
            )
        )

        record_activity(
            "stock_condition_changed",
            f"{lot.product.name}: {previous_condition} → {new_condition} "
            f"({quantity:g} {lot.product.unit}). {remark}",
        )

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not update the stock condition.", "error")
        return redirect(url_for("index"))

    flash("Stock condition updated.", "success")
    return redirect(url_for("index"))


def get_total_stock(product_id):
    return db.session.scalar(
        db.select(func.coalesce(func.sum(StockLot.quantity), 0))
        .where(
            StockLot.product_id == product_id,
            StockLot.condition.in_([
                "saleable",
                "near_expiry",
                "damaged",
                "spoiled",
                "expired",
                "quarantined",
            ]),
        )
    )


@app.route("/products/<int:product_id>/add-stock", methods=["POST"])
@login_required
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
            StockLot(
                product_id=product.id,
                quantity=quantity,
                condition="saleable",
            )
        )

        db.session.add(
            InventoryLog(
                product_id=product.id,
                action_type="stock_added",
                quantity=quantity,
                remarks=remarks or "Stock replenishment",
            )
        )

        record_activity(
            "stock_added",
            f"Added {quantity:g} {product.unit} to {product.name}",
        )

        db.session.commit()
        flash(f"Added {quantity:g} {product.unit} to {product.name}.", "success")

    except SQLAlchemyError:
        db.session.rollback()
        flash("Error updating stock.", "error")

    return redirect(url_for("index"))


@app.route("/quick-add-stock", methods=["POST"])
@login_required
def quick_add_stock():
    product_name = request.form.get("product-name", "").strip()
    raw_quantity = request.form.get("quantity", "").strip()
    remarks = request.form.get("remarks", "").strip()
    expiry_date = request.form.get("expiry_date")

    product = db.session.execute(
        select(FeedProduct).where(
            func.lower(FeedProduct.name) == product_name.lower()
        )
    ).scalar_one_or_none()

    if product is None:
        flash("Choose a product from the suggestions.", "error")
        return redirect(url_for("index"))

    try:
        quantity_decimal = Decimal(raw_quantity)
    except InvalidOperation:
        flash("Enter a valid quantity.", "error")
        return redirect(url_for("index"))

    if not quantity_decimal.is_finite() or quantity_decimal <= 0:
        flash("Enter a quantity greater than zero.", "error")
        return redirect(url_for("index"))

    quantity = float(quantity_decimal)

    parsed_expiry_date = None
    if expiry_date:
        try:
            parsed_expiry_date = datetime.strptime(
                expiry_date, "%Y-%m-%d"
            ).date()
        except ValueError:
            flash("Enter a valid expiry date.", "error")
            return redirect(url_for("index"))

    try:
        lot = StockLot(
            product_id=product.id,
            quantity=quantity,
            condition="saleable",
            expiry_date=parsed_expiry_date,
        )
        db.session.add(lot)

        product.stock_quantity += quantity
        db.session.add(
            InventoryLog(
                product_id=product.id,
                action_type="stock_added",
                quantity=quantity,
                remarks=remarks or "Quick stock addition",
            )
        )

        record_activity(
            "stock_added",
            f"Added {quantity:g} {product.unit} to {product.name}",
        )

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not update stock. Please try again.", "error")
        return redirect(url_for("index"))

    flash(f"Added {quantity:g} {product.unit} to {product.name}.", "success")
    return redirect(url_for("index"))


@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    selected_category = request.args.get("filter", "").strip()
    search = request.args.get("search", "").strip()

    categories = db.session.execute(
        select(FeedProduct.category).distinct().order_by(FeedProduct.category)
    ).scalars().all()

    query = select(FeedProduct)
    conditions = []

    if selected_category:
        conditions.append(FeedProduct.category == selected_category)

    if search:
        pattern = f"%{search}%"
        conditions.append(
            (FeedProduct.name.ilike(pattern))
            | (FeedProduct.category.ilike(pattern))
        )

    if conditions:
        query = query.where(*conditions)

    products = db.session.execute(
        query.order_by(FeedProduct.stock_quantity.desc(), FeedProduct.name)
    ).scalars().all()

    sellable_stock = {}

    for product in products:
        sellable_stock[product.id] = db.session.scalar(
            select(func.coalesce(func.sum(StockLot.quantity), 0)).where(
                StockLot.product_id == product.id,
                StockLot.quantity > 0,
                StockLot.condition.in_(SELLABLE_CONDITIONS),
            )
        ) or 0

    if request.method == "GET":
        return render_template(
            "sales.html",
            products=products,
            categories=categories,
            selected_category=selected_category,
            search=search,
            sellable_stock=sellable_stock,
        )

    requested_items = []

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

        available_stock = sellable_stock.get(product.id, 0)

        if quantity > available_stock:
            flash(
                f"Insufficient saleable stock for {product.name}. "
                f"Available: {available_stock:g} {product.unit}.",
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

            lots = db.session.execute(
                db.select(StockLot)
                .where(
                    StockLot.product_id == product.id,
                    StockLot.quantity > 0,
                    StockLot.condition.in_(SELLABLE_CONDITIONS),
                )
                .order_by(
                    StockLot.expiry_date.asc().nulls_last(),
                    StockLot.created_at.asc(),
                )
            ).scalars().all()

            remaining = quantity
            for lot in lots:
                deducted = min(remaining, lot.quantity)
                lot.quantity -= deducted
                remaining -= deducted

                if remaining <= 1e-9:
                    remaining = 0
                    break

            if remaining > 1e-9:
                raise ValueError(
                    f"Insufficient saleable stock for {product.name}. "
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

        record_activity(
            "sale_recorded",
            f"Sale #{sale.id} recorded for ₱{total:.2f}",
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

    return redirect(url_for(
        "sales",
        filter=selected_category or None,
        search=search or None
    ))


@app.route("/sales/history")
@login_required
@roles_required("admin")
def sales_history():
    all_sales = db.session.execute(
        select(Sale).order_by(Sale.timestamp.desc(), Sale.id.desc())
    ).scalars().all()

    return render_template("sales_history.html", sales=all_sales)


@app.route("/sales/export")
@login_required
@roles_required("admin")
def export_sales():
    sales = db.session.execute(
        select(Sale).order_by(Sale.timestamp, Sale.id)
    ).scalars().all()

    rows = []
    for sale in sales:
        rows.append({
            "Sale ID": sale.id,
            "Date and Time": sale.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "Number of Items": len(sale.items),
            "Total": float(sale.total_amount),
        })

    if rows:
        sales_frame = pd.DataFrame(rows)
    else:
        sales_frame = pd.DataFrame(
            columns=["Sale ID", "Date and Time", "Number of Items", "Total"]
        )

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sales_frame.to_excel(writer, sheet_name="Sales", index=False)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="sales_report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/sales/<int:sale_id>")
@login_required
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
@login_required
@roles_required("admin")
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

    daily_revenue = sales_trends["daily"]["revenue"]
    descriptive_stats = []
    if not sales_frame.empty:
        descriptive_stats = [
            _describe_values("Sale amount (₱)", sales_frame["total_amount"]),
            _describe_values("Daily revenue (₱)", daily_revenue),
        ]

    total_revenue = float(sales_frame["total_amount"].sum()) if not sales_frame.empty else 0.0
    total_sales = int(len(sales_frame))

    products = db.session.execute(
        select(FeedProduct).order_by(FeedProduct.name)
    ).scalars().all()

    product_id = request.args.get("product_id", type=int)
    if product_id is None and products:
        product_id = products[0].id

    selected_product = db.session.get(FeedProduct, product_id) if product_id else None
    forecast_data = None

    if selected_product:
        daily_sales = _get_product_daily_sales(selected_product.id, days=120)
        forecast_data = _moving_average_forecast(
            daily_sales,
            window=30,
            horizon=7,
        )

        history_dates = pd.to_datetime(daily_sales.index)
        future_dates = pd.date_range(
            start=history_dates.max() + pd.Timedelta(days=1),
            periods=7,
            freq="D",
        )

        history_values = daily_sales["units_sold"].astype(float)
        smoothed_history = (
            history_values.rolling(window=7, min_periods=1).mean().round(2)
        )

        forecast_data["labels"] = [
            date.strftime("%b %d")
            for date in list(history_dates) + list(future_dates)
        ]

        forecast_data["actual_series"] = (
            history_values.round(2).tolist() + [None] * 7
        )
        forecast_data["history_series"] = (
            smoothed_history.tolist() + [None] * 7
        )

        forecast_data["forecast_series"] = (
            [None] * (len(smoothed_history) - 1)
            + [float(smoothed_history.iloc[-1])]
            + [float(value) for value in forecast_data["forecast"]]
        )

        current_stock = float(selected_product.stock_quantity)
        average_demand = forecast_data["avg_daily_demand"]

        stock_cover_days = (
            current_stock / average_demand if average_demand > 0 else None
        )

        if stock_cover_days is None:
            risk_level = "No demand"
        elif stock_cover_days <= 7:
            risk_level = "High"
        elif stock_cover_days <= 14:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        forecast_data["current_stock"] = current_stock
        forecast_data["stock_cover_days"] = (
            round(stock_cover_days, 2) if stock_cover_days is not None else None
        )
        forecast_data["risk_level"] = risk_level

    return render_template(
        "analytics.html",
        total_sales=total_sales,
        total_revenue=round(total_revenue, 2),
        average_sale=round(total_revenue / total_sales, 2) if total_sales else 0.0,
        sales_trends=sales_trends,
        top_sellers=top_sellers,
        descriptive_stats=descriptive_stats,
        products=products,
        selected_product=selected_product,
        forecast_data=forecast_data,
    )


@app.route("/analytics/export")
@login_required
@roles_required("admin")
def export_analytics():
    # Sales data and trends
    sales_rows = db.session.execute(
        select(Sale.id, Sale.timestamp, Sale.total_amount)
        .order_by(Sale.timestamp, Sale.id)
    ).all()

    sales_frame = pd.DataFrame(
        [dict(row._mapping) for row in sales_rows],
        columns=["id", "timestamp", "total_amount"],
    )

    if not sales_frame.empty:
        sales_frame["timestamp"] = (
            pd.to_datetime(sales_frame["timestamp"], errors="coerce", utc=True)
            .dt.tz_convert(None)
        )
        sales_frame["total_amount"] = pd.to_numeric(
            sales_frame["total_amount"], errors="coerce"
        ).fillna(0.0)
        sales_frame = sales_frame.dropna(subset=["timestamp"])

    sales_trends = {
        "daily": _build_sales_trend(sales_frame, "D", "%Y-%m-%d"),
        "weekly": _build_sales_trend(sales_frame, "W-SUN", "%Y-%m-%d"),
        "monthly": _build_sales_trend(sales_frame, "M", "%b %Y"),
    }

    trend_rows = []
    for period_name, trend in sales_trends.items():
        for label, count, revenue in zip(
            trend["labels"], trend["sales"], trend["revenue"]
        ):
            trend_rows.append({
                "Period Type": period_name.title(),
                "Period": label,
                "Sales": count,
                "Revenue": revenue,
            })

    # Top-selling products
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

    items_frame = pd.DataFrame(
        [dict(row._mapping) for row in item_rows],
        columns=["quantity", "subtotal", "product_name", "unit"],
    )

    if not items_frame.empty:
        items_frame["quantity"] = pd.to_numeric(
            items_frame["quantity"], errors="coerce"
        ).fillna(0.0)
        items_frame["subtotal"] = pd.to_numeric(
            items_frame["subtotal"], errors="coerce"
        ).fillna(0.0)

        top_sellers_frame = (
            items_frame.groupby(
                ["product_name", "unit"], as_index=False
            )
            .agg(
                units_sold=("quantity", "sum"),
                revenue=("subtotal", "sum"),
            )
            .sort_values(
                ["units_sold", "revenue"],
                ascending=[False, False],
            )
            .head(5)
            .rename(columns={
                "product_name": "Product",
                "unit": "Unit",
                "units_sold": "Quantity Sold",
                "revenue": "Revenue",
            })
        )
    else:
        top_sellers_frame = pd.DataFrame(
            columns=["Product", "Unit", "Quantity Sold", "Revenue"]
        )

    # Descriptive statistics
    total_revenue = (
        float(sales_frame["total_amount"].sum())
        if not sales_frame.empty else 0.0
    )
    total_sales = len(sales_frame)
    daily_revenue = sales_trends["daily"]["revenue"]

    stats = []
    if not sales_frame.empty:
        stats = [
            _describe_values("Sale amount (₱)", sales_frame["total_amount"]),
            _describe_values("Daily revenue (₱)", daily_revenue),
        ]

    stats_frame = pd.DataFrame(stats)

    # Forecast for the currently selected product
    products = db.session.execute(
        select(FeedProduct).order_by(FeedProduct.name)
    ).scalars().all()

    product_id = request.args.get("product_id", type=int)
    if product_id is None and products:
        product_id = products[0].id

    selected_product = (
        db.session.get(FeedProduct, product_id) if product_id else None
    )

    forecast_rows = []
    forecast_summary = []

    if selected_product:
        daily_sales = _get_product_daily_sales(selected_product.id, days=120)
        forecast = _moving_average_forecast(
            daily_sales,
            window=30,
            horizon=7,
        )

        average_demand = forecast["avg_daily_demand"]
        current_stock = float(selected_product.stock_quantity)
        stock_cover_days = (
            current_stock / average_demand if average_demand > 0 else None
        )

        if stock_cover_days is None:
            risk_level = "No demand"
        elif stock_cover_days <= 7:
            risk_level = "High"
        elif stock_cover_days <= 14:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        forecast_summary.append({
            "Product": selected_product.name,
            "Current Stock": current_stock,
            "Unit": selected_product.unit,
            "Average Daily Demand": average_demand,
            "Stock Cover (Days)": stock_cover_days,
            "Risk Level": risk_level,
        })

        future_dates = pd.date_range(
            start=daily_sales.index.max() + pd.Timedelta(days=1),
            periods=len(forecast["forecast"]),
            freq="D",
        )

        forecast_rows = [
            {
                "Date": date.strftime("%Y-%m-%d"),
                "Product": selected_product.name,
                "Forecast Quantity": quantity,
                "Unit": selected_product.unit,
            }
            for date, quantity in zip(future_dates, forecast["forecast"])
        ]

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_frame = pd.DataFrame([
            {"Metric": "Sales Transactions", "Value": total_sales},
            {"Metric": "Net Sales", "Value": total_revenue},
            {
                "Metric": "Average Sale",
                "Value": total_revenue / total_sales if total_sales else 0,
            },
        ])
        summary_frame.to_excel(writer, sheet_name="Summary", index=False)

        pd.DataFrame(trend_rows).to_excel(
            writer,
            sheet_name="Sales Trends",
            index=False,
        )
        top_sellers_frame.to_excel(
            writer,
            sheet_name="Top Sellers",
            index=False,
        )
        stats_frame.to_excel(
            writer,
            sheet_name="Statistics",
            index=False,
        )
        pd.DataFrame(forecast_summary).to_excel(
            writer,
            sheet_name="Forecast Summary",
            index=False,
        )
        pd.DataFrame(forecast_rows).to_excel(
            writer,
            sheet_name="Forecast",
            index=False,
        )

    return send_excel_workbook(output, "analytics_report.xlsx")


def _get_product_daily_sales(product_id, days=120):
    end = pd.Timestamp(datetime.utcnow().date())
    start = end - pd.Timedelta(days=days - 1)
    full_index = pd.date_range(start=start, end=end, freq="D")

    cutoff = datetime.combine(start.date(), datetime.min.time())

    rows = db.session.execute(
        select(
            func.date(Sale.timestamp).label("sale_date"),
            func.sum(SaleItem.quantity).label("units_sold"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .where(
            SaleItem.product_id == product_id,
            Sale.timestamp >= cutoff,
        )
        .group_by(func.date(Sale.timestamp))
        .order_by(func.date(Sale.timestamp))
    ).all()

    df = pd.DataFrame(
        [
            {
                "date": row.sale_date,
                "units_sold": float(row.units_sold or 0),
            }
            for row in rows
        ]
    )

    if df.empty:
        return pd.DataFrame({"units_sold": 0.0}, index=full_index)

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).set_index("date").sort_index()

    return df.reindex(full_index, fill_value=0.0)


def _moving_average_forecast(daily_sales, window=7, horizon=7):
    if daily_sales.empty:
        return {
            "history": [],
            "forecast": [],
            "avg_daily_demand": 0.0,
            "projected_stock_cover_days": 0.0,
            "risk_level": "No data"
        }

    history = daily_sales["units_sold"].tolist()
    avg_daily = float(daily_sales["units_sold"].rolling(window=window, min_periods=1).mean().iloc[-1])

    forecast = []
    for i in range(horizon):
        forecast.append(round(avg_daily, 2))

    return {
        "history": [round(float(x), 2) for x in history],
        "forecast": forecast,
        "avg_daily_demand": round(avg_daily, 2),
    }


def _get_inventory_data():
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
    return inventory_movement, recent_inventory_logs, len(inventory_frame)

@app.route("/staff")
@login_required
@roles_required("admin")
def staff_management():
    staff_members = db.session.execute(
        select(User)
        .where(User.role == "staff")
        .order_by(User.username)
    ).scalars().all()

    return render_template(
        "staff_management.html",
        staff_members=staff_members
    )


@app.route("/staff/<int:user_id>/disable", methods=["POST"])
@roles_required("admin")
def disable_staff(user_id):
    staff = db.session.get(User, user_id)

    if staff is None or staff.role != "staff":
        abort(404)

    staff.enabled = False
    db.session.commit()

    flash(f"Staff account {staff.username} disabled.", "success")
    return redirect(url_for("staff_management"))


@app.route("/staff/<int:user_id>/enable", methods=["POST"])
@roles_required("admin")
def enable_staff(user_id):
    staff = db.session.get(User, user_id)

    if staff is None or staff.role != "staff":
        abort(404)

    staff.enabled = True
    db.session.commit()

    flash(f"Staff account {staff.username} enabled.", "success")
    return redirect(url_for("staff_management"))


@app.route("/staff/create", methods=["POST"])
@login_required
@roles_required("admin")
def create_staff():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("staff_management"))

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("staff_management"))

    existing = db.session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()

    if existing:
        flash("That username is already taken.", "error")
        return redirect(url_for("staff_management"))

    staff = User(username=username, role="staff", enabled=True)
    staff.set_password(password)

    try:
        db.session.add(staff)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not create the staff account. Please try again.", "error")
        return redirect(url_for("staff_management"))

    flash(f"Staff account {username} created.", "success")
    return redirect(url_for("staff_management"))


@app.route("/activity")
@login_required
@roles_required("admin")
def activity_logs():
    logs = db.session.execute(
        select(ActivityLog)
        .order_by(ActivityLog.timestamp.desc(), ActivityLog.id.desc())
        .limit(100)
    ).scalars().all()

    return render_template(
        "activity_logs.html",
        logs=logs
    )


def record_activity(action, details):
    db.session.add(
        ActivityLog(
            user_id=current_user.id,
            action=action,
            details=details,
        )
    )


if __name__ == "__main__":
    app.run(debug=True)

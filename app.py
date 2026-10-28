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


if __name__ == "__main__":
    app.run(debug=True)

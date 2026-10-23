from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from models import db, FeedProduct, InventoryLog

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///livestock.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "susi-sa-mga-it"

db.init_app(app)

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

        log = InventoryLog(
            product_id=product.id,
            action_type="stock_added",
            quantity=quantity,
            remarks=remarks or "Stock replenishment",
        )

        db.session.add(log)
        db.session.commit()

        flash(
            f"Added {quantity:g} {product.unit} to {product.name}.",
            "success",
        )

    except SQLAlchemyError:
        db.session.rollback()
        flash("Error updating stock.", "error")

    return redirect(url_for("index"))

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)

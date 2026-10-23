from app import app
from models import db, FeedProduct

PRODUCTS = [
    ("B-MEG Premium Super Biik (24kg)", "Hog Feeds", 100.50, "unit"),
    ("B-MEG Premium Super Biik (25Kg)", "Hog Feeds", 79.50, "unit"),
    ("B-MEG Premium Super Biik 24kg (per bag)", "Hog Feeds", 2373.00, "unit"),
    ("B-MEG Premium Super Biik 25kg (per bag)", "Hog Feeds", 1917.00, "unit"),
    ("B-MEG Premium Hog Pre-Starter PLT (25kg)", "Hog Feeds", 61.00, "unit"),
    ("B-MEG Premium Hog Pre-Starter 25kg (per bag)", "Hog Feeds", 1497.00, "unit"),
    ("B-MEG Premium Hog Starter Crumble (50kg)", "Hog Feeds", 46.00, "unit"),
    ("B-MEG Premium Hog Starter Crumble 50kg (per bag)", "Hog Feeds", 2193.00, "unit"),
    ("B-MEG Premium Hog Starter Pellet (50kg)", "Hog Feeds", 43.50, "unit"),
    ("B-MEG Premium Hog Starter Pellet 50kg", "Hog Feeds", 2088.00, "Sacks"),
    ("B-MEG Premium Hog Starter Grower Pellet (50kg)", "Hog Feeds", 41.00, "unit"),
    ("B-MEG Premium Hog Starter Grower Pellet 50kg", "Hog Feeds", 1957.00, "Sacks"),
    ("Porcine Digestible Peptides PDP (28kg)", "Feeds", 28.00, "unit"),
    ("Premium Chick Grower / Sari Manok 35kg", "Feeds", 35.00, "Bags"),
    ("Hog Starter Pellet 45kg", "Hog Feeds", 45.00, "Bags"),
    ("Dawa 55kg", "Feeds", 55.02, "Bags"),
    ("Rueda 7 Kinds plus", "Feeds", 39.00, "Bags"),
    ("Royal Feast 1kl 130kg", "Feeds", 130.00, "Bags"),
    ("Adult Whiskas 1kl 175kg", "Feeds", 175.00, "Bags"),
    ("Dixie 1kl 121kg", "Feeds", 131.00, "Bags"),
    ("B-Meg Premium Super Inahin 1 (gestating) 50kg", "Hog Feeds", 36.99, "Bags"),
    ("B-Meg Premium Super Inahin 1 (gestating) 50kg Sack", "Hog Feeds", 1773.00, "Sacks"),
    ("B-MEG Premium Super Inahin 2 (Lactating) 50kg", "Hog Feeds", 41.00, "Bags"),
    ("B-Meg Starter Grower 50kg", "Hog Feeds", 39.00, "Bags"),
]

with app.app_context():
    sample_products = FeedProduct.query.filter(
        db.func.lower(FeedProduct.name) == "sample"
    ).all()

    for product in sample_products:
        db.session.delete(product)

    added = 0
    skipped = 0

    for name, category, price, unit in PRODUCTS:
        existing = FeedProduct.query.filter_by(name=name).first()

        if existing:
            skipped += 1
            continue

        product = FeedProduct(
            name=name,
            category=category,
            unit_price=price,
            stock_quantity=0,
            unit=unit,
            low_stock_threshold=10,
        )
        db.session.add(product)
        added += 1

    db.session.commit()

    print("Seed completed.")
    print(f"Removed test products: {len(sample_products)}")
    print(f"Added feed products: {added}")
    print(f"Existing products skipped: {skipped}")

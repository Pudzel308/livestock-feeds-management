from app import app
from models import db, User

with app.app_context():
    existing = db.session.execute(
        db.select(User).where(User.username == "admin")
    ).scalar_one_or_none()

    if not existing:
        admin = User(username="admin", role="admin", enabled=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("Admin created: username=admin password=admin123")
    else:
        print("Admin already exists.")

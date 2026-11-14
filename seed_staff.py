from app import app
from models import db, User

with app.app_context():
    existing = db.session.execute(
        db.select(User).where(User.username == "staff")
    ).scalar_one_or_none()

    if not existing:
        admin = User(username="staff", role="staff", enabled=True)
        admin.set_password("staff123")
        db.session.add(admin)
        db.session.commit()
        print("Sdmin created: username=staff password=staff123")
    else:
        print("Staff already exists.")

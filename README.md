## Setup

In the terminal, make sure the current working directory is inside the project folder, then run:

```bash
python -m venv .venv
```

Activate the virtual environment:

**Windows:**

```bash
.venv\Scripts\activate
```

**Linux/macOS:**

```bash
source .venv/bin/activate
```

Install the required packages:

```bash
python -m pip install -r requirements.txt
```

## Setup the database

```bash
python seed.py
python seed_admin.py
python seed_staff.py
```

## Run

```bash
python app.py
```

Open:

<http://127.0.0.1:5000>

import sqlite3


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS publishers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_email TEXT,
    address TEXT,
    tax_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    address TEXT,
    bio TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    publisher_id INTEGER NOT NULL,
    isbn TEXT UNIQUE,
    genre TEXT,
    language TEXT DEFAULT 'Spanish',
    publication_date DATE,
    format TEXT DEFAULT 'both' CHECK(format IN ('digital', 'physical', 'both')),
    cover_price_digital REAL DEFAULT 0.0,
    cover_price_physical REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (publisher_id) REFERENCES publishers(id)
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    royalty_rate REAL NOT NULL DEFAULT 10.0,
    role TEXT DEFAULT 'author',
    PRIMARY KEY (book_id, author_id),
    FOREIGN KEY (book_id) REFERENCES books(id),
    FOREIGN KEY (author_id) REFERENCES authors(id)
);

CREATE TABLE IF NOT EXISTS print_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    cost_per_unit REAL DEFAULT 0.0,
    total_cost REAL DEFAULT 0.0,
    print_date DATE,
    supplier TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id)
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL UNIQUE,
    units_physical_in_stock INTEGER DEFAULT 0,
    units_physical_sold INTEGER DEFAULT 0,
    units_digital_sold INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id)
);

CREATE TABLE IF NOT EXISTS revenue_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    channel TEXT DEFAULT 'direct',
    period_month INTEGER NOT NULL,
    period_year INTEGER NOT NULL,
    units_sold INTEGER DEFAULT 0,
    revenue_amount REAL DEFAULT 0.0,
    format TEXT DEFAULT 'digital' CHECK(format IN ('digital', 'physical')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, channel, format, period_month, period_year),
    FOREIGN KEY (book_id) REFERENCES books(id)
);

CREATE TABLE IF NOT EXISTS expense_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER,
    category_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    description TEXT,
    expense_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id),
    FOREIGN KEY (category_id) REFERENCES expense_categories(id)
);

CREATE TABLE IF NOT EXISTS royalty_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    period TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'paid')),
    paid_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(author_id, book_id, period),
    FOREIGN KEY (author_id) REFERENCES authors(id),
    FOREIGN KEY (book_id) REFERENCES books(id)
);
"""

SEED_CATEGORIES = [
    ('Editorial', 'Editing, proofreading, manuscript review'),
    ('Design & Production', 'Cover design, interior layout, typography'),
    ('Printing & Distribution', 'Print runs, shipping, warehousing'),
    ('Marketing & Advertising', 'Ads, promotions, book fairs, social media'),
    ('General & Admin', 'Office, legal, accounting, miscellaneous'),
]


def init_db(db):
    db.executescript(SCHEMA)
    existing = db.execute('SELECT COUNT(*) FROM expense_categories').fetchone()[0]
    if existing == 0:
        db.executemany(
            'INSERT INTO expense_categories (name, description) VALUES (?, ?)',
            SEED_CATEGORIES
        )
    db.commit()

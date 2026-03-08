import csv
import io
import sqlite3

import openpyxl
from datetime import datetime, date

from flask import (
    Flask, g, redirect, render_template, request,
    url_for, flash, jsonify
)

import models

DATABASE = 'finance.db'
app = Flask(__name__)
app.secret_key = 'publisher-admin-secret-key-change-in-production'


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def dashboard():
    return render_template('dashboard.html')


@app.route('/api/dashboard-stats')
def dashboard_stats():
    db = get_db()
    current_year = datetime.now().year

    total_revenue_all = db.execute(
        'SELECT COALESCE(SUM(revenue_amount), 0) FROM revenue_entries'
    ).fetchone()[0]

    total_revenue_year = db.execute(
        'SELECT COALESCE(SUM(revenue_amount), 0) FROM revenue_entries WHERE period_year = ?',
        [current_year]
    ).fetchone()[0]

    total_expenses_year = db.execute(
        'SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE strftime("%Y", expense_date) = ?',
        [str(current_year)]
    ).fetchone()[0]

    royalties_pending = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM royalty_payments WHERE status = 'pending'"
    ).fetchone()[0]

    total_books = db.execute('SELECT COUNT(*) FROM books').fetchone()[0]

    physical_stock_value = db.execute(
        '''SELECT COALESCE(SUM(i.units_physical_in_stock * b.cover_price_physical), 0)
           FROM inventory i JOIN books b ON i.book_id = b.id'''
    ).fetchone()[0]

    top_books = db.execute(
        '''SELECT b.title, COALESCE(SUM(re.revenue_amount), 0) as total_rev
           FROM books b
           LEFT JOIN revenue_entries re ON b.id = re.book_id
           GROUP BY b.id, b.title
           ORDER BY total_rev DESC LIMIT 5'''
    ).fetchall()

    monthly_data = db.execute(
        '''SELECT period_year, period_month, SUM(revenue_amount) as rev
           FROM revenue_entries
           GROUP BY period_year, period_month
           ORDER BY period_year DESC, period_month DESC LIMIT 12'''
    ).fetchall()

    monthly_expenses = db.execute(
        '''SELECT strftime("%Y", expense_date) as yr,
                  strftime("%m", expense_date) as mo,
                  SUM(amount) as exp
           FROM expenses
           GROUP BY yr, mo
           ORDER BY yr DESC, mo DESC LIMIT 12'''
    ).fetchall()

    expense_by_cat = db.execute(
        '''SELECT ec.name, COALESCE(SUM(e.amount), 0) as total
           FROM expense_categories ec
           LEFT JOIN expenses e ON ec.id = e.category_id
           GROUP BY ec.id, ec.name'''
    ).fetchall()

    return jsonify({
        'total_revenue_all': round(total_revenue_all, 2),
        'total_revenue_year': round(total_revenue_year, 2),
        'total_expenses_year': round(total_expenses_year, 2),
        'net_profit_year': round(total_revenue_year - total_expenses_year, 2),
        'royalties_pending': round(royalties_pending, 2),
        'total_books': total_books,
        'physical_stock_value': round(physical_stock_value, 2),
        'top_books': [{'title': r['title'], 'revenue': round(r['total_rev'], 2)} for r in top_books],
        'monthly_revenue': [
            {'year': r['period_year'], 'month': r['period_month'], 'revenue': round(r['rev'], 2)}
            for r in monthly_data
        ],
        'monthly_expenses': [
            {'year': int(r['yr']), 'month': int(r['mo']), 'expenses': round(r['exp'], 2)}
            for r in monthly_expenses
        ],
        'expense_by_category': [
            {'category': r['name'], 'total': round(r['total'], 2)} for r in expense_by_cat
        ],
    })


# ---------------------------------------------------------------------------
# Catalog (Books)
# ---------------------------------------------------------------------------

@app.route('/catalog')
def catalog():
    db = get_db()
    books = db.execute(
        '''SELECT b.*, p.name as publisher_name,
                  GROUP_CONCAT(a.name, ', ') as authors
           FROM books b
           JOIN publishers p ON b.publisher_id = p.id
           LEFT JOIN book_authors ba ON b.id = ba.book_id
           LEFT JOIN authors a ON ba.author_id = a.id
           GROUP BY b.id
           ORDER BY b.title'''
    ).fetchall()
    publishers = db.execute('SELECT id, name FROM publishers ORDER BY name').fetchall()
    return render_template('catalog.html', books=books, publishers=publishers)


@app.route('/catalog/new', methods=['POST'])
def catalog_new():
    db = get_db()
    title = request.form.get('title', '').strip()
    publisher_id = request.form.get('publisher_id')
    isbn = request.form.get('isbn', '').strip() or None
    genre = request.form.get('genre', '').strip() or None
    language = request.form.get('language', 'Spanish').strip()
    publication_date = request.form.get('publication_date') or None
    fmt = request.form.get('format', 'both')
    try:
        price_digital = float(request.form.get('cover_price_digital', 0) or 0)
        price_physical = float(request.form.get('cover_price_physical', 0) or 0)
    except ValueError:
        flash('Cover prices must be valid numbers.', 'error')
        return redirect(url_for('catalog'))
    if not title or not publisher_id:
        flash('Title and publisher are required.', 'error')
        return redirect(url_for('catalog'))
    try:
        cur = db.execute(
            '''INSERT INTO books (title, publisher_id, isbn, genre, language,
               publication_date, format, cover_price_digital, cover_price_physical)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [title, publisher_id, isbn, genre, language, publication_date,
             fmt, price_digital, price_physical]
        )
        book_id = cur.lastrowid
        db.execute(
            'INSERT OR IGNORE INTO inventory (book_id) VALUES (?)', [book_id]
        )
        db.commit()
        flash(f'Book "{title}" added to catalog.', 'success')
    except sqlite3.IntegrityError:
        flash('A book with that ISBN already exists.', 'error')
    return redirect(url_for('catalog'))


@app.route('/catalog/<int:book_id>')
def catalog_detail(book_id):
    db = get_db()
    book = db.execute(
        '''SELECT b.*, p.name as publisher_name
           FROM books b JOIN publishers p ON b.publisher_id = p.id
           WHERE b.id = ?''', [book_id]
    ).fetchone()
    if not book:
        flash('Book not found.', 'error')
        return redirect(url_for('catalog'))

    book_authors = db.execute(
        '''SELECT a.id, a.name, ba.royalty_rate, ba.role
           FROM book_authors ba JOIN authors a ON ba.author_id = a.id
           WHERE ba.book_id = ?''', [book_id]
    ).fetchall()

    all_authors = db.execute(
        'SELECT id, name FROM authors ORDER BY name'
    ).fetchall()

    inventory = db.execute(
        'SELECT * FROM inventory WHERE book_id = ?', [book_id]
    ).fetchone()

    print_runs = db.execute(
        'SELECT * FROM print_runs WHERE book_id = ? ORDER BY print_date DESC', [book_id]
    ).fetchall()

    revenue = db.execute(
        '''SELECT * FROM revenue_entries WHERE book_id = ?
           ORDER BY period_year DESC, period_month DESC''', [book_id]
    ).fetchall()

    expenses = db.execute(
        '''SELECT e.*, ec.name as category_name
           FROM expenses e JOIN expense_categories ec ON e.category_id = ec.id
           WHERE e.book_id = ?
           ORDER BY e.expense_date DESC''', [book_id]
    ).fetchall()

    royalties = db.execute(
        '''SELECT rp.*, a.name as author_name
           FROM royalty_payments rp JOIN authors a ON rp.author_id = a.id
           WHERE rp.book_id = ?
           ORDER BY rp.period DESC''', [book_id]
    ).fetchall()

    return render_template('catalog_detail.html',
                           book=book,
                           book_authors=book_authors,
                           all_authors=all_authors,
                           inventory=inventory,
                           print_runs=print_runs,
                           revenue=revenue,
                           expenses=expenses,
                           royalties=royalties)


@app.route('/catalog/<int:book_id>/edit', methods=['POST'])
def catalog_edit(book_id):
    db = get_db()
    data = request.get_json()
    isbn = data.get('isbn', '').strip() or None
    try:
        price_digital = float(data.get('cover_price_digital', 0) or 0)
        price_physical = float(data.get('cover_price_physical', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid price values'}), 400
    try:
        db.execute(
            '''UPDATE books SET title=?, isbn=?, genre=?, language=?,
               publication_date=?, format=?, cover_price_digital=?, cover_price_physical=?
               WHERE id=?''',
            [data.get('title'), isbn, data.get('genre'), data.get('language'),
             data.get('publication_date') or None, data.get('format'),
             price_digital, price_physical, book_id]
        )
        db.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'ISBN already exists'}), 409


@app.route('/catalog/<int:book_id>/delete', methods=['POST'])
def catalog_delete(book_id):
    db = get_db()
    rev_count = db.execute(
        'SELECT COUNT(*) FROM revenue_entries WHERE book_id=?', [book_id]
    ).fetchone()[0]
    if rev_count > 0:
        flash(f'Cannot delete: book has {rev_count} revenue entry(ies). Delete them first.', 'error')
        return redirect(url_for('catalog_detail', book_id=book_id))
    db.execute('DELETE FROM book_authors WHERE book_id=?', [book_id])
    db.execute('DELETE FROM inventory WHERE book_id=?', [book_id])
    db.execute('DELETE FROM print_runs WHERE book_id=?', [book_id])
    db.execute('DELETE FROM books WHERE id=?', [book_id])
    db.commit()
    flash('Book deleted.', 'success')
    return redirect(url_for('catalog'))


@app.route('/catalog/<int:book_id>/authors/add', methods=['POST'])
def catalog_add_author(book_id):
    db = get_db()
    author_id = request.form.get('author_id')
    try:
        royalty_rate = float(request.form.get('royalty_rate', 10) or 10)
    except ValueError:
        royalty_rate = 10.0
    role = request.form.get('role', 'author')
    try:
        db.execute(
            'INSERT INTO book_authors (book_id, author_id, royalty_rate, role) VALUES (?, ?, ?, ?)',
            [book_id, author_id, royalty_rate, role]
        )
        db.commit()
        flash('Author added to book.', 'success')
    except sqlite3.IntegrityError:
        flash('Author already assigned to this book.', 'error')
    return redirect(url_for('catalog_detail', book_id=book_id))


@app.route('/catalog/<int:book_id>/authors/<int:author_id>/remove', methods=['POST'])
def catalog_remove_author(book_id, author_id):
    db = get_db()
    db.execute(
        'DELETE FROM book_authors WHERE book_id=? AND author_id=?', [book_id, author_id]
    )
    db.commit()
    flash('Author removed from book.', 'success')
    return redirect(url_for('catalog_detail', book_id=book_id))


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

@app.route('/authors')
def authors():
    db = get_db()
    authors_list = db.execute(
        '''SELECT a.*,
                  COUNT(DISTINCT ba.book_id) as book_count,
                  COALESCE(SUM(rp.amount) FILTER (WHERE rp.status='pending'), 0) as pending_royalties
           FROM authors a
           LEFT JOIN book_authors ba ON a.id = ba.author_id
           LEFT JOIN royalty_payments rp ON a.id = rp.author_id
           GROUP BY a.id
           ORDER BY a.name'''
    ).fetchall()
    return render_template('authors.html', authors=authors_list)


@app.route('/authors/new', methods=['POST'])
def authors_new():
    db = get_db()
    name = request.form.get('name', '').strip()
    if not name:
        flash('Author name is required.', 'error')
        return redirect(url_for('authors'))
    db.execute(
        'INSERT INTO authors (name, email, phone, address, bio) VALUES (?, ?, ?, ?, ?)',
        [name,
         request.form.get('email', '').strip() or None,
         request.form.get('phone', '').strip() or None,
         request.form.get('address', '').strip() or None,
         request.form.get('bio', '').strip() or None]
    )
    db.commit()
    flash(f'Author "{name}" added.', 'success')
    return redirect(url_for('authors'))


@app.route('/authors/<int:author_id>')
def author_detail(author_id):
    db = get_db()
    author = db.execute('SELECT * FROM authors WHERE id=?', [author_id]).fetchone()
    if not author:
        flash('Author not found.', 'error')
        return redirect(url_for('authors'))
    books = db.execute(
        '''SELECT b.id, b.title, ba.royalty_rate, ba.role
           FROM book_authors ba JOIN books b ON ba.book_id = b.id
           WHERE ba.author_id = ?
           ORDER BY b.title''', [author_id]
    ).fetchall()
    royalties = db.execute(
        '''SELECT rp.*, b.title as book_title
           FROM royalty_payments rp JOIN books b ON rp.book_id = b.id
           WHERE rp.author_id = ?
           ORDER BY rp.period DESC''', [author_id]
    ).fetchall()
    return render_template('author_detail.html', author=author, books=books, royalties=royalties)


@app.route('/authors/<int:author_id>/edit', methods=['POST'])
def authors_edit(author_id):
    db = get_db()
    data = request.get_json()
    db.execute(
        'UPDATE authors SET name=?, email=?, phone=?, address=?, bio=? WHERE id=?',
        [data.get('name'), data.get('email') or None, data.get('phone') or None,
         data.get('address') or None, data.get('bio') or None, author_id]
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/authors/<int:author_id>/delete', methods=['POST'])
def authors_delete(author_id):
    db = get_db()
    count = db.execute(
        'SELECT COUNT(*) FROM book_authors WHERE author_id=?', [author_id]
    ).fetchone()[0]
    if count > 0:
        flash(f'Cannot delete: author is linked to {count} book(s).', 'error')
        return redirect(url_for('author_detail', author_id=author_id))
    db.execute('DELETE FROM authors WHERE id=?', [author_id])
    db.commit()
    flash('Author deleted.', 'success')
    return redirect(url_for('authors'))


# ---------------------------------------------------------------------------
# Publishers (admin)
# ---------------------------------------------------------------------------

@app.route('/publishers')
def publishers():
    db = get_db()
    pubs = db.execute(
        '''SELECT p.*, COUNT(b.id) as book_count
           FROM publishers p LEFT JOIN books b ON p.id = b.publisher_id
           GROUP BY p.id ORDER BY p.name'''
    ).fetchall()
    return render_template('publishers.html', publishers=pubs)


@app.route('/publishers/new', methods=['POST'])
def publishers_new():
    db = get_db()
    name = request.form.get('name', '').strip()
    if not name:
        flash('Publisher name is required.', 'error')
        return redirect(url_for('publishers'))
    db.execute(
        'INSERT INTO publishers (name, contact_email, address, tax_id) VALUES (?, ?, ?, ?)',
        [name,
         request.form.get('contact_email', '').strip() or None,
         request.form.get('address', '').strip() or None,
         request.form.get('tax_id', '').strip() or None]
    )
    db.commit()
    flash(f'Publisher "{name}" added.', 'success')
    return redirect(url_for('publishers'))


@app.route('/publishers/<int:pub_id>/edit', methods=['POST'])
def publishers_edit(pub_id):
    db = get_db()
    data = request.get_json()
    db.execute(
        'UPDATE publishers SET name=?, contact_email=?, address=?, tax_id=? WHERE id=?',
        [data.get('name'), data.get('contact_email') or None,
         data.get('address') or None, data.get('tax_id') or None, pub_id]
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/publishers/<int:pub_id>/delete', methods=['POST'])
def publishers_delete(pub_id):
    db = get_db()
    count = db.execute(
        'SELECT COUNT(*) FROM books WHERE publisher_id=?', [pub_id]
    ).fetchone()[0]
    if count > 0:
        flash(f'Cannot delete: publisher has {count} book(s).', 'error')
        return redirect(url_for('publishers'))
    db.execute('DELETE FROM publishers WHERE id=?', [pub_id])
    db.commit()
    flash('Publisher deleted.', 'success')
    return redirect(url_for('publishers'))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@app.route('/inventory')
def inventory():
    db = get_db()
    items = db.execute(
        '''SELECT b.id as book_id, b.title, b.format, b.cover_price_physical,
                  i.units_physical_in_stock, i.units_physical_sold, i.units_digital_sold,
                  i.updated_at
           FROM books b
           LEFT JOIN inventory i ON b.id = i.book_id
           ORDER BY b.title'''
    ).fetchall()
    print_runs = db.execute(
        '''SELECT pr.*, b.title as book_title
           FROM print_runs pr JOIN books b ON pr.book_id = b.id
           ORDER BY pr.print_date DESC LIMIT 20'''
    ).fetchall()
    books = db.execute('SELECT id, title FROM books ORDER BY title').fetchall()
    return render_template('inventory.html', items=items, print_runs=print_runs, books=books)


@app.route('/inventory/print-run/new', methods=['POST'])
def inventory_print_run_new():
    db = get_db()
    book_id = request.form.get('book_id')
    try:
        quantity = int(request.form.get('quantity', 0) or 0)
        cost_per_unit = float(request.form.get('cost_per_unit', 0) or 0)
        total_cost = quantity * cost_per_unit
    except ValueError:
        flash('Quantity and cost must be valid numbers.', 'error')
        return redirect(url_for('inventory'))
    print_date = request.form.get('print_date') or date.today().isoformat()
    db.execute(
        '''INSERT INTO print_runs (book_id, quantity, cost_per_unit, total_cost, print_date, supplier, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        [book_id, quantity, cost_per_unit, total_cost, print_date,
         request.form.get('supplier', '').strip() or None,
         request.form.get('notes', '').strip() or None]
    )
    db.execute(
        '''INSERT INTO inventory (book_id, units_physical_in_stock)
           VALUES (?, ?)
           ON CONFLICT(book_id) DO UPDATE SET
             units_physical_in_stock = units_physical_in_stock + excluded.units_physical_in_stock,
             updated_at = CURRENT_TIMESTAMP''',
        [book_id, quantity]
    )
    db.commit()
    flash(f'Print run of {quantity} units recorded.', 'success')
    return redirect(url_for('inventory'))


@app.route('/inventory/<int:book_id>/adjust', methods=['POST'])
def inventory_adjust(book_id):
    db = get_db()
    try:
        adjustment = int(request.form.get('adjustment', 0))
    except ValueError:
        flash('Adjustment must be an integer.', 'error')
        return redirect(url_for('inventory'))
    db.execute(
        '''INSERT INTO inventory (book_id, units_physical_in_stock)
           VALUES (?, MAX(0, ?))
           ON CONFLICT(book_id) DO UPDATE SET
             units_physical_in_stock = MAX(0, units_physical_in_stock + excluded.units_physical_in_stock),
             updated_at = CURRENT_TIMESTAMP''',
        [book_id, adjustment]
    )
    db.commit()
    flash('Inventory adjusted.', 'success')
    return redirect(url_for('inventory'))


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------

@app.route('/revenue')
def revenue():
    db = get_db()
    entries = db.execute(
        '''SELECT re.*, b.title as book_title
           FROM revenue_entries re JOIN books b ON re.book_id = b.id
           ORDER BY re.period_year DESC, re.period_month DESC, b.title'''
    ).fetchall()
    books = db.execute('SELECT id, title FROM books ORDER BY title').fetchall()
    return render_template('revenue.html', entries=entries, books=books,
                           now_year=datetime.now().year)


@app.route('/revenue/new', methods=['POST'])
def revenue_new():
    db = get_db()
    book_id = request.form.get('book_id')
    channel = request.form.get('channel', 'direct')
    fmt = request.form.get('format', 'digital')
    try:
        period_month = int(request.form.get('period_month', 1))
        period_year = int(request.form.get('period_year', datetime.now().year))
        units_sold = int(request.form.get('units_sold', 0) or 0)
        revenue_amount = float(request.form.get('revenue_amount', 0) or 0)
    except ValueError:
        flash('Invalid numeric values.', 'error')
        return redirect(url_for('revenue'))
    if not book_id:
        flash('Book is required.', 'error')
        return redirect(url_for('revenue'))
    try:
        db.execute(
            '''INSERT INTO revenue_entries
               (book_id, channel, period_month, period_year, units_sold, revenue_amount, format)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            [book_id, channel, period_month, period_year, units_sold, revenue_amount, fmt]
        )
        # Update inventory
        if fmt == 'digital':
            db.execute(
                '''INSERT INTO inventory (book_id, units_digital_sold)
                   VALUES (?, ?)
                   ON CONFLICT(book_id) DO UPDATE SET
                     units_digital_sold = units_digital_sold + excluded.units_digital_sold,
                     updated_at = CURRENT_TIMESTAMP''',
                [book_id, units_sold]
            )
        else:
            db.execute(
                '''INSERT INTO inventory (book_id, units_physical_sold, units_physical_in_stock)
                   VALUES (?, ?, ?)
                   ON CONFLICT(book_id) DO UPDATE SET
                     units_physical_sold = units_physical_sold + excluded.units_physical_sold,
                     units_physical_in_stock = MAX(0, units_physical_in_stock - excluded.units_physical_sold),
                     updated_at = CURRENT_TIMESTAMP''',
                [book_id, units_sold, 0]
            )
        db.commit()
        flash('Revenue entry added.', 'success')
    except sqlite3.IntegrityError:
        flash('A revenue entry already exists for this book/channel/format/period. Delete it first to update.', 'error')
    return redirect(url_for('revenue'))


@app.route('/revenue/<int:entry_id>/delete', methods=['POST'])
def revenue_delete(entry_id):
    db = get_db()
    entry = db.execute('SELECT * FROM revenue_entries WHERE id=?', [entry_id]).fetchone()
    if entry:
        if entry['format'] == 'digital':
            db.execute(
                '''UPDATE inventory SET units_digital_sold = MAX(0, units_digital_sold - ?),
                   updated_at = CURRENT_TIMESTAMP WHERE book_id=?''',
                [entry['units_sold'], entry['book_id']]
            )
        else:
            db.execute(
                '''UPDATE inventory SET
                   units_physical_sold = MAX(0, units_physical_sold - ?),
                   units_physical_in_stock = units_physical_in_stock + ?,
                   updated_at = CURRENT_TIMESTAMP WHERE book_id=?''',
                [entry['units_sold'], entry['units_sold'], entry['book_id']]
            )
        db.execute('DELETE FROM revenue_entries WHERE id=?', [entry_id])
        db.commit()
        flash('Revenue entry deleted.', 'success')
    return redirect(url_for('revenue'))


@app.route('/revenue/import', methods=['POST'])
def revenue_import():
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Por favor sube un archivo .csv válido.', 'error')
        return redirect(url_for('revenue'))

    content = file.read()
    if len(content) > 5 * 1024 * 1024:
        flash('Archivo demasiado grande (máx 5 MB).', 'error')
        return redirect(url_for('revenue'))

    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    rows = [{k.strip().lower(): v.strip() for k, v in row.items()} for row in reader]

    db = get_db()
    processed = imported = skipped = 0
    errors = []

    for i, row in enumerate(rows, 1):
        try:
            title = row.get('libro', '').strip()
            if not title:
                skipped += 1
                continue

            book = db.execute('SELECT id FROM books WHERE title=?', [title]).fetchone()
            if not book:
                errors.append(f'Fila {i}: Libro "{title}" no encontrado en catálogo')
                skipped += 1
                continue
            book_id = book['id']

            channel = row.get('canal', 'direct').strip() or 'direct'
            fmt = row.get('formato', 'digital').strip() or 'digital'

            mes_str = row.get('mes', '').strip()
            año_str = row.get('año', '').strip()
            if not mes_str or not año_str:
                errors.append(f'Fila {i}: Falta mes o año')
                skipped += 1
                continue

            period_month = int(float(mes_str))
            period_year = int(float(año_str))
            units_sold = int(float(row.get('unidades', '0') or 0))
            revenue_amount = float(
                str(row.get('ingreso', '0') or 0).replace('$', '').replace(',', '')
            )

            processed += 1

            try:
                db.execute(
                    '''INSERT INTO revenue_entries
                       (book_id, channel, period_month, period_year, units_sold, revenue_amount, format)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    [book_id, channel, period_month, period_year, units_sold, revenue_amount, fmt]
                )
                if fmt == 'digital':
                    db.execute(
                        '''INSERT INTO inventory (book_id, units_digital_sold) VALUES (?, ?)
                           ON CONFLICT(book_id) DO UPDATE SET
                             units_digital_sold = units_digital_sold + excluded.units_digital_sold,
                             updated_at = CURRENT_TIMESTAMP''',
                        [book_id, units_sold]
                    )
                else:
                    db.execute(
                        '''INSERT INTO inventory (book_id, units_physical_sold, units_physical_in_stock)
                           VALUES (?, ?, 0)
                           ON CONFLICT(book_id) DO UPDATE SET
                             units_physical_sold = units_physical_sold + excluded.units_physical_sold,
                             units_physical_in_stock = MAX(0, units_physical_in_stock - excluded.units_physical_sold),
                             updated_at = CURRENT_TIMESTAMP''',
                        [book_id, units_sold]
                    )
                imported += 1
            except sqlite3.IntegrityError:
                skipped += 1

        except (ValueError, TypeError) as e:
            errors.append(f'Fila {i}: Valores inválidos — {e}')
            skipped += 1

    db.commit()

    msg = f'Importación completa: {processed} filas procesadas, {imported} importadas, {skipped} omitidas.'
    if errors:
        msg += f' ({len(errors)} advertencias)'
    flash(msg, 'success')

    books = db.execute('SELECT id, title FROM books ORDER BY title').fetchall()
    entries = db.execute(
        '''SELECT re.*, b.title as book_title
           FROM revenue_entries re JOIN books b ON re.book_id = b.id
           ORDER BY re.period_year DESC, re.period_month DESC, b.title'''
    ).fetchall()
    return render_template('revenue.html', entries=entries, books=books,
                           now_year=datetime.now().year,
                           import_result={'processed': processed, 'imported': imported,
                                          'skipped': skipped},
                           import_errors=errors)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

@app.route('/expenses')
def expenses():
    db = get_db()
    entries = db.execute(
        '''SELECT e.*, ec.name as category_name, b.title as book_title
           FROM expenses e
           JOIN expense_categories ec ON e.category_id = ec.id
           LEFT JOIN books b ON e.book_id = b.id
           ORDER BY e.expense_date DESC'''
    ).fetchall()
    categories = db.execute('SELECT * FROM expense_categories ORDER BY name').fetchall()
    books = db.execute('SELECT id, title FROM books ORDER BY title').fetchall()
    return render_template('expenses.html', entries=entries, categories=categories, books=books,
                           today=date.today().isoformat())


@app.route('/expenses/new', methods=['POST'])
def expenses_new():
    db = get_db()
    category_id = request.form.get('category_id')
    book_id = request.form.get('book_id') or None
    description = request.form.get('description', '').strip() or None
    expense_date = request.form.get('expense_date') or date.today().isoformat()
    try:
        amount = float(request.form.get('amount', 0))
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash('Amount must be a positive number.', 'error')
        return redirect(url_for('expenses'))
    if not category_id:
        flash('Category is required.', 'error')
        return redirect(url_for('expenses'))
    db.execute(
        'INSERT INTO expenses (book_id, category_id, amount, description, expense_date) VALUES (?, ?, ?, ?, ?)',
        [book_id, category_id, amount, description, expense_date]
    )
    db.commit()
    flash('Expense recorded.', 'success')
    return redirect(url_for('expenses'))


@app.route('/expenses/import', methods=['POST'])
def expenses_import():
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Por favor sube un archivo .csv válido.', 'error')
        return redirect(url_for('expenses'))

    content = file.read()
    if len(content) > 5 * 1024 * 1024:
        flash('Archivo demasiado grande (máx 5 MB).', 'error')
        return redirect(url_for('expenses'))

    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    rows = [{k.strip().lower(): v.strip() for k, v in row.items()} for row in reader]

    db = get_db()
    # Build lookup maps (case-insensitive)
    categories = {r['name'].lower(): r['id']
                  for r in db.execute('SELECT id, name FROM expense_categories').fetchall()}
    books_map = {r['title'].lower(): r['id']
                 for r in db.execute('SELECT id, title FROM books').fetchall()}

    processed = imported = skipped = 0
    errors = []

    for i, row in enumerate(rows, 1):
        try:
            cat_name = row.get('categoria', '').strip()
            if not cat_name:
                errors.append(f'Fila {i}: Falta categoría')
                skipped += 1
                continue

            category_id = categories.get(cat_name.lower())
            if not category_id:
                errors.append(f'Fila {i}: Categoría "{cat_name}" no encontrada')
                skipped += 1
                continue

            monto_str = row.get('monto', '').strip()
            if not monto_str:
                errors.append(f'Fila {i}: Falta monto')
                skipped += 1
                continue

            amount = float(monto_str.replace('$', '').replace(',', ''))
            if amount <= 0:
                errors.append(f'Fila {i}: El monto debe ser positivo')
                skipped += 1
                continue

            book_title = row.get('libro', '').strip()
            book_id = books_map.get(book_title.lower()) if book_title else None

            description = row.get('descripcion', '').strip() or None
            expense_date = row.get('fecha', '').strip() or date.today().isoformat()

            processed += 1
            db.execute(
                'INSERT INTO expenses (book_id, category_id, amount, description, expense_date) VALUES (?, ?, ?, ?, ?)',
                [book_id, category_id, amount, description, expense_date]
            )
            imported += 1

        except (ValueError, TypeError) as e:
            errors.append(f'Fila {i}: Valores inválidos — {e}')
            skipped += 1

    db.commit()

    msg = f'Importación completa: {processed} filas procesadas, {imported} importadas, {skipped} omitidas.'
    if errors:
        msg += f' ({len(errors)} advertencias)'
    flash(msg, 'success')

    entries = db.execute(
        '''SELECT e.*, ec.name as category_name, b.title as book_title
           FROM expenses e
           JOIN expense_categories ec ON e.category_id = ec.id
           LEFT JOIN books b ON e.book_id = b.id
           ORDER BY e.expense_date DESC'''
    ).fetchall()
    cats = db.execute('SELECT * FROM expense_categories ORDER BY name').fetchall()
    all_books = db.execute('SELECT id, title FROM books ORDER BY title').fetchall()
    return render_template('expenses.html', entries=entries, categories=cats, books=all_books,
                           today=date.today().isoformat(),
                           import_result={'processed': processed, 'imported': imported,
                                          'skipped': skipped},
                           import_errors=errors)


@app.route('/expenses/<int:expense_id>/edit', methods=['POST'])
def expenses_edit(expense_id):
    db = get_db()
    data = request.get_json()
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid amount'}), 400
    db.execute(
        '''UPDATE expenses SET category_id=?, book_id=?, amount=?, description=?, expense_date=?
           WHERE id=?''',
        [data.get('category_id'), data.get('book_id') or None,
         amount, data.get('description') or None,
         data.get('expense_date'), expense_id]
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/expenses/<int:expense_id>/delete', methods=['POST'])
def expenses_delete(expense_id):
    db = get_db()
    db.execute('DELETE FROM expenses WHERE id=?', [expense_id])
    db.commit()
    flash('Expense deleted.', 'success')
    return redirect(url_for('expenses'))


# ---------------------------------------------------------------------------
# Royalties
# ---------------------------------------------------------------------------

@app.route('/royalties')
def royalties():
    db = get_db()
    payments = db.execute(
        '''SELECT rp.*, a.name as author_name, b.title as book_title
           FROM royalty_payments rp
           JOIN authors a ON rp.author_id = a.id
           JOIN books b ON rp.book_id = b.id
           ORDER BY rp.period DESC, a.name'''
    ).fetchall()
    return render_template('royalties.html', payments=payments, now_year=datetime.now().year)


@app.route('/royalties/generate', methods=['POST'])
def royalties_generate():
    db = get_db()
    try:
        period_month = int(request.form.get('period_month', 1))
        period_year = int(request.form.get('period_year', datetime.now().year))
    except ValueError:
        flash('Invalid period.', 'error')
        return redirect(url_for('royalties'))

    period_str = f'{period_year:04d}-{period_month:02d}'

    rows = db.execute(
        '''SELECT re.book_id, re.revenue_amount, ba.author_id, ba.royalty_rate
           FROM revenue_entries re
           JOIN book_authors ba ON re.book_id = ba.book_id
           WHERE re.period_month = ? AND re.period_year = ?''',
        [period_month, period_year]
    ).fetchall()

    if not rows:
        flash(f'No revenue entries found for {period_str}.', 'warning')
        return redirect(url_for('royalties'))

    inserted = 0
    skipped = 0
    for row in rows:
        amount = round(row['revenue_amount'] * row['royalty_rate'] / 100, 2)
        try:
            db.execute(
                '''INSERT INTO royalty_payments (author_id, book_id, amount, period)
                   VALUES (?, ?, ?, ?)''',
                [row['author_id'], row['book_id'], amount, period_str]
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1

    db.commit()
    flash(f'Royalties generated for {period_str}: {inserted} new, {skipped} already existed.', 'success')
    return redirect(url_for('royalties'))


@app.route('/royalties/<int:payment_id>/mark-paid', methods=['POST'])
def royalties_mark_paid(payment_id):
    db = get_db()
    db.execute(
        "UPDATE royalty_payments SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=?",
        [payment_id]
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/royalties/<int:payment_id>/delete', methods=['POST'])
def royalties_delete(payment_id):
    db = get_db()
    db.execute('DELETE FROM royalty_payments WHERE id=?', [payment_id])
    db.commit()
    flash('Royalty payment deleted.', 'success')
    return redirect(url_for('royalties'))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route('/reports')
def reports():
    return render_template('reports.html')


@app.route('/api/reports')
def api_reports():
    db = get_db()
    year = request.args.get('year', type=int)

    year_filter_rev = 'WHERE period_year = ?' if year else ''
    year_filter_exp = 'WHERE strftime("%Y", expense_date) = ?' if year else ''
    params_rev = [year] if year else []
    params_exp = [str(year)] if year else []

    monthly_pl = db.execute(
        f'''SELECT period_year, period_month,
                   SUM(revenue_amount) as revenue
            FROM revenue_entries {year_filter_rev}
            GROUP BY period_year, period_month
            ORDER BY period_year, period_month''',
        params_rev
    ).fetchall()

    monthly_exp = db.execute(
        f'''SELECT strftime("%Y", expense_date) as yr,
                   strftime("%m", expense_date) as mo,
                   SUM(amount) as expenses
            FROM expenses {year_filter_exp}
            GROUP BY yr, mo
            ORDER BY yr, mo''',
        params_exp
    ).fetchall()

    book_profitability = db.execute(
        '''SELECT b.id, b.title,
                  COALESCE(SUM(re.revenue_amount), 0) as total_revenue,
                  COALESCE((SELECT SUM(e.amount) FROM expenses e WHERE e.book_id = b.id), 0) as total_expenses,
                  COALESCE((SELECT SUM(rp.amount) FROM royalty_payments rp
                             WHERE rp.book_id = b.id AND rp.status = 'paid'), 0) as royalties_paid
           FROM books b
           LEFT JOIN revenue_entries re ON b.id = re.book_id
           GROUP BY b.id, b.title
           ORDER BY total_revenue DESC'''
    ).fetchall()

    royalty_summary = db.execute(
        '''SELECT a.name as author_name,
                  COALESCE(SUM(rp.amount) FILTER (WHERE rp.status='pending'), 0) as pending,
                  COALESCE(SUM(rp.amount) FILTER (WHERE rp.status='paid'), 0) as paid
           FROM authors a
           LEFT JOIN royalty_payments rp ON a.id = rp.author_id
           GROUP BY a.id, a.name
           ORDER BY a.name'''
    ).fetchall()

    available_years = db.execute(
        '''SELECT DISTINCT period_year FROM revenue_entries
           UNION SELECT DISTINCT CAST(strftime("%Y", expense_date) AS INTEGER) FROM expenses
           ORDER BY 1 DESC'''
    ).fetchall()

    return jsonify({
        'monthly_pl': [
            {'year': r['period_year'], 'month': r['period_month'], 'revenue': round(r['revenue'], 2)}
            for r in monthly_pl
        ],
        'monthly_expenses': [
            {'year': int(r['yr']), 'month': int(r['mo']), 'expenses': round(r['expenses'], 2)}
            for r in monthly_exp
        ],
        'book_profitability': [
            {
                'id': r['id'],
                'title': r['title'],
                'revenue': round(r['total_revenue'], 2),
                'expenses': round(r['total_expenses'], 2),
                'royalties_paid': round(r['royalties_paid'], 2),
                'net': round(r['total_revenue'] - r['total_expenses'] - r['royalties_paid'], 2),
            }
            for r in book_profitability
        ],
        'royalty_summary': [
            {'author': r['author_name'], 'pending': round(r['pending'], 2), 'paid': round(r['paid'], 2)}
            for r in royalty_summary
        ],
        'available_years': [r[0] for r in available_years],
    })


# ---------------------------------------------------------------------------
# KDP CSV Import
# ---------------------------------------------------------------------------

@app.route('/import/kdp', methods=['GET', 'POST'])
def import_kdp():
    if request.method == 'GET':
        return render_template('import_kdp.html')

    file = request.files.get('csv_file')
    fname = file.filename if file else ''
    is_xlsx = fname.lower().endswith('.xlsx')
    is_csv = fname.lower().endswith('.csv')
    if not file or (not is_csv and not is_xlsx):
        flash('Por favor sube un archivo .csv o .xlsx válido.', 'error')
        return redirect(url_for('import_kdp'))

    content = file.read()
    if len(content) > 5 * 1024 * 1024:
        flash('Archivo demasiado grande (máx 5 MB).', 'error')
        return redirect(url_for('import_kdp'))

    db = get_db()

    # Ensure KDP publisher exists
    kdp_pub = db.execute(
        "SELECT id FROM publishers WHERE name='Amazon KDP'"
    ).fetchone()
    if not kdp_pub:
        cur = db.execute(
            "INSERT INTO publishers (name, contact_email) VALUES ('Amazon KDP', 'kdp@amazon.com')"
        )
        kdp_pub_id = cur.lastrowid
    else:
        kdp_pub_id = kdp_pub['id']

    processed = 0
    imported = 0
    skipped = 0
    errors = []

    if is_xlsx:
        # --- Excel format (Amazon KDP Spanish UI) ---
        try:
            period_month, period_year, excel_rows = _parse_kdp_excel(content)
        except ValueError as e:
            flash(str(e), 'error')
            return redirect(url_for('import_kdp'))

        for i, row in enumerate(excel_rows, 1):
            try:
                title = row['title']
                author_name = row['author']
                isbn = row['isbn']
                plan_pago = row['plan_pago']
                units_str = row['units_str']
                ingresos_str = row['ingresos_str']

                if not title:
                    skipped += 1
                    continue

                # Determine format from plan de pago
                plan_lower = plan_pago.lower()
                if 'tapa blanda' in plan_lower:
                    entry_format = 'physical'
                else:
                    entry_format = 'digital'

                # For KENP, units are pages (not unit sales) — store as 0
                is_kenp = 'kenp' in plan_lower or 'páginas' in plan_lower
                try:
                    units_sold = 0 if is_kenp else int(float(units_str or 0))
                    revenue_amount = float(
                        str(ingresos_str).replace('$', '').replace(',', '') or 0
                    )
                except ValueError:
                    errors.append(f'Fila {i}: Valores numéricos inválidos')
                    skipped += 1
                    continue

                processed += 1

                # Find or create author
                author_id = None
                if author_name:
                    author = db.execute(
                        'SELECT id FROM authors WHERE name=?', [author_name]
                    ).fetchone()
                    if author:
                        author_id = author['id']
                    else:
                        cur = db.execute(
                            'INSERT INTO authors (name) VALUES (?)', [author_name]
                        )
                        author_id = cur.lastrowid

                # Determine book format for creation
                if entry_format == 'physical':
                    book_fmt = 'physical'
                else:
                    book_fmt = 'digital'

                # Find or create book
                book = db.execute(
                    'SELECT id FROM books WHERE title=? AND publisher_id=?',
                    [title, kdp_pub_id]
                ).fetchone()
                if book:
                    book_id = book['id']
                else:
                    try:
                        cur = db.execute(
                            'INSERT INTO books (title, publisher_id, isbn, format) VALUES (?, ?, ?, ?)',
                            [title, kdp_pub_id, isbn, book_fmt]
                        )
                        book_id = cur.lastrowid
                        db.execute(
                            'INSERT OR IGNORE INTO inventory (book_id) VALUES (?)', [book_id]
                        )
                    except sqlite3.IntegrityError:
                        book = db.execute(
                            'SELECT id FROM books WHERE isbn=?', [isbn]
                        ).fetchone()
                        book_id = book['id'] if book else None
                        if not book_id:
                            skipped += 1
                            continue

                # Link author to book
                if author_id:
                    db.execute(
                        'INSERT OR IGNORE INTO book_authors (book_id, author_id) VALUES (?, ?)',
                        [book_id, author_id]
                    )

                # Upsert revenue entry — aggregate multiple stores into one row
                db.execute(
                    '''INSERT INTO revenue_entries
                       (book_id, channel, period_month, period_year, units_sold, revenue_amount, format)
                       VALUES (?, 'kdp', ?, ?, ?, ?, ?)
                       ON CONFLICT(book_id, channel, format, period_month, period_year) DO UPDATE SET
                         units_sold = units_sold + excluded.units_sold,
                         revenue_amount = revenue_amount + excluded.revenue_amount''',
                    [book_id, period_month, period_year, units_sold, revenue_amount, entry_format]
                )

                # Update inventory
                if entry_format == 'physical':
                    db.execute(
                        '''INSERT INTO inventory (book_id, units_physical_sold)
                           VALUES (?, ?)
                           ON CONFLICT(book_id) DO UPDATE SET
                             units_physical_sold = units_physical_sold + excluded.units_physical_sold,
                             updated_at = CURRENT_TIMESTAMP''',
                        [book_id, units_sold]
                    )
                else:
                    db.execute(
                        '''INSERT INTO inventory (book_id, units_digital_sold)
                           VALUES (?, ?)
                           ON CONFLICT(book_id) DO UPDATE SET
                             units_digital_sold = units_digital_sold + excluded.units_digital_sold,
                             updated_at = CURRENT_TIMESTAMP''',
                        [book_id, units_sold]
                    )
                imported += 1

            except Exception as e:
                errors.append(f'Fila {i}: Error inesperado - {str(e)}')
                skipped += 1

    else:
        # --- CSV format (Amazon KDP English UI, legacy) ---
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('latin-1')

        reader = csv.DictReader(io.StringIO(text))
        rows = [{k.strip(): v.strip() for k, v in row.items()} for row in reader]

        for i, row in enumerate(rows, 1):
            try:
                title = row.get('Title', '').strip()
                author_name = row.get('Author', '').strip()
                asin = row.get('ASIN', '').strip() or None
                transaction_month = row.get('Transaction Month', '').strip()
                units_str = row.get('Net Units Sold', row.get('Units Sold', '0')).strip()
                royalty_str = row.get('Royalty', '0').strip()

                if not title or not transaction_month:
                    skipped += 1
                    continue

                period_month, period_year = _parse_kdp_period(transaction_month)
                if not period_month:
                    errors.append(f'Row {i}: Cannot parse period "{transaction_month}"')
                    skipped += 1
                    continue

                try:
                    units_sold = int(float(units_str or 0))
                    revenue_amount = float(royalty_str.replace('$', '').replace(',', '') or 0)
                except ValueError:
                    errors.append(f'Row {i}: Invalid numeric values')
                    skipped += 1
                    continue

                processed += 1

                author_id = None
                if author_name:
                    author = db.execute(
                        'SELECT id FROM authors WHERE name=?', [author_name]
                    ).fetchone()
                    if author:
                        author_id = author['id']
                    else:
                        cur = db.execute(
                            'INSERT INTO authors (name) VALUES (?)', [author_name]
                        )
                        author_id = cur.lastrowid

                book = db.execute(
                    'SELECT id FROM books WHERE title=? AND publisher_id=?',
                    [title, kdp_pub_id]
                ).fetchone()
                if book:
                    book_id = book['id']
                else:
                    isbn = asin or None
                    try:
                        cur = db.execute(
                            '''INSERT INTO books (title, publisher_id, isbn, format)
                               VALUES (?, ?, ?, 'digital')''',
                            [title, kdp_pub_id, isbn]
                        )
                        book_id = cur.lastrowid
                        db.execute(
                            'INSERT OR IGNORE INTO inventory (book_id) VALUES (?)', [book_id]
                        )
                    except sqlite3.IntegrityError:
                        book = db.execute('SELECT id FROM books WHERE isbn=?', [isbn]).fetchone()
                        book_id = book['id'] if book else None
                        if not book_id:
                            skipped += 1
                            continue

                if author_id:
                    db.execute(
                        'INSERT OR IGNORE INTO book_authors (book_id, author_id) VALUES (?, ?)',
                        [book_id, author_id]
                    )

                try:
                    db.execute(
                        '''INSERT INTO revenue_entries
                           (book_id, channel, period_month, period_year, units_sold, revenue_amount, format)
                           VALUES (?, 'kdp', ?, ?, ?, ?, 'digital')''',
                        [book_id, period_month, period_year, units_sold, revenue_amount]
                    )
                    db.execute(
                        '''INSERT INTO inventory (book_id, units_digital_sold)
                           VALUES (?, ?)
                           ON CONFLICT(book_id) DO UPDATE SET
                             units_digital_sold = units_digital_sold + excluded.units_digital_sold,
                             updated_at = CURRENT_TIMESTAMP''',
                        [book_id, units_sold]
                    )
                    imported += 1
                except sqlite3.IntegrityError:
                    skipped += 1

            except Exception as e:
                errors.append(f'Row {i}: Unexpected error - {str(e)}')
                skipped += 1

    db.commit()

    flash(f'Importación completa: {processed} filas procesadas, {imported} importadas, {skipped} omitidas.', 'success')
    return render_template('import_kdp.html',
                           result={'processed': processed, 'imported': imported, 'skipped': skipped},
                           errors=errors)


def _parse_kdp_period(s):
    """Parse KDP period string to (month, year). Returns (None, None) on failure."""
    import re
    s = s.strip()
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
        'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
        'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
    }
    # "January 2025" or "Enero 2025"
    m = re.match(r'(\w+)\s+(\d{4})', s, re.IGNORECASE)
    if m:
        mon = months.get(m.group(1).lower())
        if mon:
            return mon, int(m.group(2))
    # "2025-01"
    m = re.match(r'(\d{4})-(\d{2})', s)
    if m:
        return int(m.group(2)), int(m.group(1))
    # "01/2025"
    m = re.match(r'(\d{1,2})/(\d{4})', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _parse_kdp_excel(content):
    """Parse an Amazon KDP Excel file (Spanish UI format).

    Returns (period_month, period_year, rows) where rows is a list of dicts with keys:
    title, author, isbn, plan_pago, units_netas, ingresos.
    Raises ValueError if the period or header row cannot be found.
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    # Use the last sheet (usually "ingresos totales")
    ws = wb.worksheets[-1]

    period_month = None
    period_year = None
    header_row_idx = None
    col_map = {}  # column name → column index (0-based)

    all_rows = list(ws.iter_rows(values_only=True))

    for row_idx, row in enumerate(all_rows):
        row_vals = [str(c).strip() if c is not None else '' for c in row]

        # Detect period row: first cell contains "Periodo de ventas"
        if period_month is None:
            for ci, val in enumerate(row_vals):
                if 'periodo de ventas' in val.lower():
                    # Period value is in the next non-empty cell on the same row
                    for cv in row_vals[ci + 1:]:
                        if cv:
                            period_month, period_year = _parse_kdp_period(cv)
                            break
                    break

        # Detect header row: contains "Título"
        if header_row_idx is None:
            for ci, val in enumerate(row_vals):
                if val.lower() in ('título', 'titulo'):
                    header_row_idx = row_idx
                    for j, h in enumerate(row_vals):
                        col_map[h.lower()] = j
                    break

        if period_month and header_row_idx is not None:
            break

    if not period_month:
        raise ValueError('No se encontró "Periodo de ventas" en el archivo Excel.')
    if header_row_idx is None:
        raise ValueError('No se encontró la fila de encabezados (Título, Autor…) en el archivo Excel.')

    # Helper to find a column index by partial name match
    def _col(keywords):
        for key in keywords:
            for h, idx in col_map.items():
                if key in h:
                    return idx
        return None

    idx_title = _col(['título', 'titulo'])
    idx_author = _col(['autor'])
    idx_isbn = _col(['asin', 'isbn'])
    idx_plan = _col(['plan de pago', 'plan'])
    idx_units = _col(['unidades netas', 'kenp'])
    idx_ingresos = _col(['ingresos'])

    rows = []
    for row in all_rows[header_row_idx + 1:]:
        row_vals = [str(c).strip() if c is not None else '' for c in row]
        # Skip empty rows
        if not any(row_vals):
            continue
        title = row_vals[idx_title] if idx_title is not None else ''
        if not title or title.lower() in ('none', ''):
            continue
        author = row_vals[idx_author] if idx_author is not None else ''
        isbn = row_vals[idx_isbn] if idx_isbn is not None else ''
        plan = row_vals[idx_plan] if idx_plan is not None else ''
        units_str = row_vals[idx_units] if idx_units is not None else '0'
        ingresos_str = row_vals[idx_ingresos] if idx_ingresos is not None else '0'

        rows.append({
            'title': title,
            'author': author.strip(),
            'isbn': isbn if isbn not in ('', 'None', 'N/A') else None,
            'plan_pago': plan,
            'units_str': units_str,
            'ingresos_str': ingresos_str,
        })

    return period_month, period_year, rows


# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------

with app.app_context():
    _db = get_db()
    models.init_db(_db)
    close_db(None)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

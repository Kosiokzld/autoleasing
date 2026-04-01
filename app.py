# -*- coding: utf-8 -*-
"""
АВТОЛИЗИНГ v2.0 — Уеб базирана система за управление на договори
"""

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from datetime import datetime, date, timedelta
import sqlite3
import os
import sys
import calendar
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import io

app = Flask(__name__)
app.secret_key = 'autoleasing_secret_2026'

# ============================================
# БАЗА ДАННИ
# ============================================
def get_db_path():
    if getattr(sys, 'frozen', False):
        app_path = os.path.dirname(sys.executable)
    else:
        app_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(app_path, 'auto_leasing.db')

DB_PATH = get_db_path()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT UNIQUE NOT NULL,
        reg_number TEXT,
        brand_model TEXT,
        client_name TEXT,
        client_egn TEXT,
        phone TEXT,
        address TEXT,
        email TEXT,
        contract_date TEXT,
        total_amount REAL DEFAULT 0,
        initial_payment REAL DEFAULT 0,
        num_installments INTEGER DEFAULT 1,
        monthly_payment REAL DEFAULT 0,
        payment_day INTEGER DEFAULT 1,
        status TEXT DEFAULT 'Активен'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS installments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        installment_num INTEGER,
        due_date TEXT,
        amount REAL,
        paid REAL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        expense_type TEXT,
        description TEXT,
        expense_date TEXT,
        due_date TEXT,
        amount REAL,
        paid REAL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        payment_date TEXT,
        amount REAL,
        note TEXT,
        for_fines REAL DEFAULT 0,
        for_insurance REAL DEFAULT 0,
        for_penalties REAL DEFAULT 0,
        for_other REAL DEFAULT 0,
        for_overdue REAL DEFAULT 0,
        for_regular REAL DEFAULT 0,
        unallocated REAL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS smtp_settings (
        id INTEGER PRIMARY KEY,
        smtp_server TEXT,
        smtp_port INTEGER DEFAULT 587,
        smtp_user TEXT,
        smtp_password TEXT,
        sender_name TEXT,
        sender_email TEXT
    )''')
    # Миграции
    for col, definition in [('address', 'TEXT'), ('email', 'TEXT')]:
        try:
            c.execute(f'ALTER TABLE contracts ADD COLUMN {col} {definition}')
        except:
            pass
    for col in ['for_other', 'for_penalties']:
        try:
            c.execute(f'ALTER TABLE payments ADD COLUMN {col} REAL DEFAULT 0')
        except:
            pass
    try:
        c.execute('ALTER TABLE contracts ADD COLUMN grace_days INTEGER DEFAULT 0')
    except:
        pass
    conn.commit()
    conn.close()

# ============================================
# ПОМОЩНИ ФУНКЦИИ
# ============================================
def parse_number(text):
    if not text or str(text).strip() == '':
        return 0.0
    text = str(text).strip().replace(' ', '').replace(',', '.')
    try:
        return float(text)
    except:
        return 0.0

def parse_date(text):
    if not text or str(text).strip() == '':
        return None
    text = str(text).strip()
    for fmt in ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%y']:
        try:
            return datetime.strptime(text, fmt).date()
        except:
            continue
    return None

def format_date(d):
    if isinstance(d, str):
        d = parse_date(d)
    if d:
        return d.strftime('%d.%m.%Y')
    return ''

def format_num(n):
    try:
        n = float(n)
        formatted = f"{n:,.2f}".replace(',', ' ').replace('.', ',')
        return formatted
    except:
        return '0,00'

def today_str():
    return date.today().strftime('%d.%m.%Y')

def get_contract_list(conn):
    """Връща списък с договори за падащо меню: [{id, label}, ...]"""
    rows = conn.execute("SELECT contract_id, client_name, reg_number, brand_model FROM contracts ORDER BY contract_id").fetchall()
    result = []
    for r in rows:
        parts = [r['contract_id']]
        if r['client_name']:
            parts.append(r['client_name'])
        if r['reg_number']:
            parts.append(r['reg_number'])
        elif r['brand_model']:
            parts.append(r['brand_model'])
        result.append({'id': r['contract_id'], 'label': ' - '.join(parts)})
    return result

# ============================================
# ГЕНЕРИРАНЕ НА ПОГАСИТЕЛЕН ПЛАН
# ============================================
def generate_installments(contract_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT contract_date, initial_payment, num_installments,
                        monthly_payment, payment_day, grace_days
                 FROM contracts WHERE contract_id=?""", (contract_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    contract_date_str, init_pay, num_inst, monthly, pay_day, grace_days = row
    c.execute("DELETE FROM installments WHERE contract_id=?", (contract_id,))
    start = parse_date(contract_date_str)
    if not start:
        conn.close()
        return
    pay_day = int(pay_day) if pay_day else 1
    grace_days = int(grace_days) if grace_days else 0
    # Първата вноска: дата на договор + отсрочка, после на деня на падежа
    first_due = start + timedelta(days=grace_days) if grace_days > 0 else None
    month = start.month
    year = start.year
    for i in range(1, int(num_inst) + 1):
        if i == 1 and first_due:
            # Първата вноска е на датата на договора + дни отсрочка
            d = first_due
        else:
            if i == 1:
                month += 1
            elif i == 2 and first_due:
                # Втората вноска: от месеца на първата вноска + 1
                month = first_due.month + 1
                year = first_due.year
            else:
                month += 1
            if month > 12:
                month = 1
                year += 1
            max_day = calendar.monthrange(year, month)[1]
            d = date(year, month, min(pay_day, max_day))
        c.execute("""INSERT INTO installments (contract_id, installment_num, due_date, amount, paid)
                     VALUES (?,?,?,?,0)""", (contract_id, i, d.strftime('%Y-%m-%d'), monthly))
    conn.commit()
    conn.close()

# ============================================
# РАЗПРЕДЕЛЕНИЕ НА ПЛАЩАНЕ
# ============================================
def distribute_payment(contract_id, payment_date_str, amount, note=''):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    remaining = round(amount, 2)
    for_fines = 0.0
    for_insurance = 0.0
    for_penalties = 0.0
    for_other = 0.0
    for_overdue = 0.0
    for_regular = 0.0

    # 1. Глоби
    c.execute("""SELECT id, amount, paid FROM expenses
                 WHERE contract_id=? AND expense_type='Глоба'
                 AND (due_date IS NULL OR due_date<=?) AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        eid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE expenses SET paid=paid+? WHERE id=?", (pay, eid))
        for_fines += pay
        remaining = round(remaining - pay, 2)

    # 2. Застраховки
    c.execute("""SELECT id, amount, paid FROM expenses
                 WHERE contract_id=? AND expense_type='Застраховка'
                 AND (due_date IS NULL OR due_date<=?) AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        eid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE expenses SET paid=paid+? WHERE id=?", (pay, eid))
        for_insurance += pay
        remaining = round(remaining - pay, 2)

    # 3. Неустойки
    c.execute("""SELECT id, amount, paid FROM expenses
                 WHERE contract_id=? AND expense_type='Неустойка'
                 AND (due_date IS NULL OR due_date<=?) AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        eid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE expenses SET paid=paid+? WHERE id=?", (pay, eid))
        for_penalties += pay
        remaining = round(remaining - pay, 2)

    # 4. Други разходи
    c.execute("""SELECT id, amount, paid FROM expenses
                 WHERE contract_id=? AND expense_type NOT IN ('Глоба','Застраховка','Неустойка')
                 AND (due_date IS NULL OR due_date<=?) AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        eid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE expenses SET paid=paid+? WHERE id=?", (pay, eid))
        for_other += pay
        remaining = round(remaining - pay, 2)

    # 5. Просрочени вноски
    c.execute("""SELECT id, amount, paid FROM installments
                 WHERE contract_id=? AND due_date<? AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        iid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE installments SET paid=paid+? WHERE id=?", (pay, iid))
        for_overdue += pay
        remaining = round(remaining - pay, 2)

    # 6. Редовни вноски
    c.execute("""SELECT id, amount, paid FROM installments
                 WHERE contract_id=? AND due_date>=? AND amount>paid
                 ORDER BY due_date ASC""", (contract_id, payment_date_str))
    for row in c.fetchall():
        if remaining <= 0: break
        iid, amt, paid = row
        owed = round(amt - paid, 2)
        pay = min(remaining, owed)
        c.execute("UPDATE installments SET paid=paid+? WHERE id=?", (pay, iid))
        for_regular += pay
        remaining = round(remaining - pay, 2)

    unallocated = round(remaining, 2)
    c.execute("""INSERT INTO payments
        (contract_id, payment_date, amount, note,
         for_fines, for_insurance, for_penalties, for_other,
         for_overdue, for_regular, unallocated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (contract_id, payment_date_str, amount, note,
         for_fines, for_insurance, for_penalties, for_other,
         for_overdue, for_regular, unallocated))
    conn.commit()
    conn.close()
    return {
        'for_fines': for_fines, 'for_insurance': for_insurance,
        'for_penalties': for_penalties, 'for_other': for_other,
        'for_overdue': for_overdue, 'for_regular': for_regular,
        'unallocated': unallocated
    }

# ============================================
# МАРШРУТИ — ГЛАВНА СТРАНИЦА
# ============================================
@app.route('/')
def index():
    conn = get_conn()
    today = date.today()
    today_db = today.strftime('%Y-%m-%d')
    # Общ брой активни договори
    total_contracts = conn.execute("SELECT COUNT(*) FROM contracts WHERE status='Активен'").fetchone()[0]
    # Договори с просрочие
    overdue_contracts = conn.execute("""SELECT DISTINCT c.contract_id, c.client_name, c.reg_number
        FROM contracts c JOIN installments i ON c.contract_id=i.contract_id
        WHERE c.status='Активен' AND i.due_date < ? AND i.amount > i.paid
        ORDER BY c.contract_id""", (today_db,)).fetchall()
    # Предстоящи падежи (следващите 7 дни)
    next_week = (today + timedelta(days=7)).strftime('%Y-%m-%d')
    upcoming = conn.execute("""SELECT c.contract_id, c.client_name, c.reg_number, i.due_date,
        ROUND(i.amount - i.paid, 2) as remaining
        FROM contracts c JOIN installments i ON c.contract_id=i.contract_id
        WHERE c.status='Активен' AND i.due_date >= ? AND i.due_date <= ? AND i.amount > i.paid
        ORDER BY i.due_date""", (today_db, next_week)).fetchall()
    upcoming_list = []
    for u in upcoming:
        upcoming_list.append({
            'contract_id': u['contract_id'], 'client_name': u['client_name'] or '',
            'reg_number': u['reg_number'] or '', 'due_date': format_date(u['due_date']),
            'remaining': format_num(u['remaining'])
        })
    # Общо просрочени суми
    total_overdue = conn.execute("""SELECT COALESCE(SUM(i.amount - i.paid), 0)
        FROM installments i JOIN contracts c ON i.contract_id=c.contract_id
        WHERE c.status='Активен' AND i.due_date < ? AND i.amount > i.paid""", (today_db,)).fetchone()[0]
    overdue_list = []
    for o in overdue_contracts:
        # Сума на просрочие за този договор
        amt = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM installments WHERE contract_id=? AND due_date < ? AND amount > paid",
                           (o['contract_id'], today_db)).fetchone()[0]
        overdue_list.append({
            'contract_id': o['contract_id'], 'client_name': o['client_name'] or '',
            'reg_number': o['reg_number'] or '', 'amount': format_num(amt)
        })
    conn.close()
    return render_template('dashboard.html',
                           total_contracts=total_contracts,
                           overdue_list=overdue_list,
                           upcoming_list=upcoming_list,
                           total_overdue=format_num(total_overdue),
                           today=format_date(today))

# ============================================
# МАРШРУТИ — ДОГОВОРИ
# ============================================
@app.route('/contracts')
def contracts():
    conn = get_conn()
    rows = conn.execute("""SELECT contract_id, reg_number, brand_model, client_name,
                                  phone, contract_date, total_amount, monthly_payment,
                                  num_installments, status
                           FROM contracts ORDER BY contract_id""").fetchall()
    conn.close()
    contracts_list = []
    for r in rows:
        contracts_list.append({
            'contract_id': r['contract_id'],
            'reg_number': r['reg_number'] or '',
            'brand_model': r['brand_model'] or '',
            'client_name': r['client_name'] or '',
            'phone': r['phone'] or '',
            'contract_date': format_date(r['contract_date']),
            'total_amount': format_num(r['total_amount']),
            'monthly_payment': format_num(r['monthly_payment']),
            'num_installments': r['num_installments'],
            'status': r['status'] or 'Активен'
        })
    return render_template('contracts.html', contracts=contracts_list)

@app.route('/contracts/new', methods=['GET', 'POST'])
def contract_new():
    if request.method == 'POST':
        v = {k: request.form.get(k, '').strip() for k in [
            'contract_id', 'reg_number', 'brand_model', 'client_name',
            'client_egn', 'phone', 'address', 'email', 'contract_date',
            'total_amount', 'initial_payment', 'num_installments',
            'monthly_payment', 'payment_day', 'grace_days', 'status'
        ]}
        if not v['contract_id']:
            flash('Въведете номер на договор.', 'danger')
            return render_template('contract_form.html', data=v, edit=False)
        d = parse_date(v['contract_date'])
        if not d:
            flash('Невалидна дата. Формат: дд.мм.гггг', 'danger')
            return render_template('contract_form.html', data=v, edit=False)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""INSERT INTO contracts
                (contract_id, reg_number, brand_model, client_name, client_egn,
                 phone, address, email, contract_date, total_amount,
                 initial_payment, num_installments, monthly_payment, payment_day, grace_days, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (v['contract_id'], v['reg_number'], v['brand_model'], v['client_name'],
                 v['client_egn'], v['phone'], v['address'], v['email'],
                 d.strftime('%Y-%m-%d'), parse_number(v['total_amount']),
                 parse_number(v['initial_payment']), int(v['num_installments'] or 1),
                 parse_number(v['monthly_payment']), int(v['payment_day'] or 1),
                 int(v['grace_days'] or 0), v['status'] or 'Активен'))
            conn.commit()
        except sqlite3.IntegrityError:
            flash(f'Договор {v["contract_id"]} вече съществува.', 'danger')
            conn.close()
            return render_template('contract_form.html', data=v, edit=False)
        conn.close()
        if request.form.get('generate_plan'):
            generate_installments(v['contract_id'])
        flash(f'Договор {v["contract_id"]} е създаден успешно.', 'success')
        return redirect(url_for('contracts'))
    return render_template('contract_form.html', data={}, edit=False)

@app.route('/contracts/edit/<contract_id>', methods=['GET', 'POST'])
def contract_edit(contract_id):
    conn = get_conn()
    if request.method == 'POST':
        v = {k: request.form.get(k, '').strip() for k in [
            'reg_number', 'brand_model', 'client_name', 'client_egn',
            'phone', 'address', 'email', 'contract_date',
            'total_amount', 'initial_payment', 'num_installments',
            'monthly_payment', 'payment_day', 'grace_days', 'status'
        ]}
        d = parse_date(v['contract_date'])
        if not d:
            flash('Невалидна дата.', 'danger')
            v['contract_id'] = contract_id
            conn.close()
            return render_template('contract_form.html', data=v, edit=True)
        conn.execute("""UPDATE contracts SET
            reg_number=?, brand_model=?, client_name=?, client_egn=?,
            phone=?, address=?, email=?, contract_date=?,
            total_amount=?, initial_payment=?, num_installments=?,
            monthly_payment=?, payment_day=?, grace_days=?, status=?
            WHERE contract_id=?""",
            (v['reg_number'], v['brand_model'], v['client_name'], v['client_egn'],
             v['phone'], v['address'], v['email'], d.strftime('%Y-%m-%d'),
             parse_number(v['total_amount']), parse_number(v['initial_payment']),
             int(v['num_installments'] or 1), parse_number(v['monthly_payment']),
             int(v['payment_day'] or 1), int(v['grace_days'] or 0),
             v['status'] or 'Активен',
             contract_id))
        conn.commit()
        conn.close()
        if request.form.get('generate_plan'):
            generate_installments(contract_id)
        flash(f'Договор {contract_id} е обновен.', 'success')
        return redirect(url_for('contracts'))
    row = conn.execute("SELECT * FROM contracts WHERE contract_id=?", (contract_id,)).fetchone()
    conn.close()
    if not row:
        flash('Договорът не е намерен.', 'danger')
        return redirect(url_for('contracts'))
    data = dict(row)
    d = parse_date(str(data['contract_date']))
    data['contract_date'] = d.strftime('%Y-%m-%d') if d else ''
    return render_template('contract_form.html', data=data, edit=True)

@app.route('/contracts/delete/<contract_id>', methods=['POST'])
def contract_delete(contract_id):
    conn = sqlite3.connect(DB_PATH)
    for tbl in ['contracts', 'installments', 'expenses', 'payments']:
        conn.execute(f"DELETE FROM {tbl} WHERE contract_id=?", (contract_id,))
    conn.commit()
    conn.close()
    flash(f'Договор {contract_id} е изтрит.', 'warning')
    return redirect(url_for('contracts'))

# ============================================
# МАРШРУТИ — РАЗХОДИ
# ============================================
@app.route('/expenses')
def expenses():
    conn = get_conn()
    contract_list = get_contract_list(conn)
    cid = request.args.get('contract_id', '')
    expenses_list = []
    if cid:
        rows = conn.execute("""SELECT id, expense_type, description, expense_date, due_date, amount, paid
                               FROM expenses WHERE contract_id=? ORDER BY due_date, expense_date""", (cid,)).fetchall()
        for r in rows:
            remaining = round(r['amount'] - r['paid'], 2)
            expenses_list.append({
                'id': r['id'], 'expense_type': r['expense_type'],
                'description': r['description'] or '',
                'expense_date': format_date(r['expense_date']),
                'due_date': format_date(r['due_date']),
                'amount': format_num(r['amount']),
                'paid': format_num(r['paid']),
                'remaining': format_num(remaining),
                'status': 'paid' if remaining <= 0 else ('partial' if r['paid'] > 0 else 'unpaid')
            })
    conn.close()
    return render_template('expenses.html', contracts=contract_list, selected=cid, expenses=expenses_list)

@app.route('/expenses/add', methods=['POST'])
def expense_add():
    cid = request.form.get('contract_id', '')
    if not cid:
        flash('Изберете договор.', 'danger')
        return redirect(url_for('expenses'))
    exp_date = parse_date(request.form.get('expense_date', '')) or date.today()
    due_date = parse_date(request.form.get('due_date', ''))
    amount = parse_number(request.form.get('amount', ''))
    if amount <= 0:
        flash('Въведете сума над нула.', 'danger')
        return redirect(url_for('expenses', contract_id=cid))
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT INTO expenses
        (contract_id, expense_type, description, expense_date, due_date, amount, paid)
        VALUES (?,?,?,?,?,?,0)""",
        (cid, request.form.get('expense_type', 'Друго'),
         request.form.get('description', ''),
         exp_date.strftime('%Y-%m-%d'),
         due_date.strftime('%Y-%m-%d') if due_date else None,
         amount))
    conn.commit()
    conn.close()
    flash('Разходът е добавен.', 'success')
    return redirect(url_for('expenses', contract_id=cid))

@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
def expense_delete(expense_id):
    cid = request.form.get('contract_id', '')
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    conn.commit()
    conn.close()
    flash('Разходът е изтрит.', 'warning')
    return redirect(url_for('expenses', contract_id=cid))

# ============================================
# МАРШРУТИ — ПЛАЩАНИЯ
# ============================================
@app.route('/payments')
def payments():
    conn = get_conn()
    contract_list = get_contract_list(conn)
    cid = request.args.get('contract_id', '')
    payments_list = []
    installments_list = []
    if cid:
        rows = conn.execute("""SELECT id, payment_date, amount, for_fines, for_insurance,
                                      for_penalties, for_other, for_overdue, for_regular, unallocated, note
                               FROM payments WHERE contract_id=? ORDER BY payment_date""", (cid,)).fetchall()
        for r in rows:
            payments_list.append({
                'id': r['id'],
                'payment_date': format_date(r['payment_date']),
                'amount': format_num(r['amount']),
                'for_fines': format_num(r['for_fines']),
                'for_insurance': format_num(r['for_insurance']),
                'for_penalties': format_num(r['for_penalties']),
                'for_other': format_num(r['for_other']),
                'for_overdue': format_num(r['for_overdue']),
                'for_regular': format_num(r['for_regular']),
                'unallocated': format_num(r['unallocated']),
                'note': r['note'] or ''
            })
        # Погасителен план
        inst_rows = conn.execute("""SELECT installment_num, due_date, amount, paid
                                    FROM installments WHERE contract_id=? ORDER BY installment_num""", (cid,)).fetchall()
        today = date.today()
        for r in inst_rows:
            rem = round(r['amount'] - r['paid'], 2)
            d = parse_date(str(r['due_date']))
            if rem <= 0:
                status, css = 'Платена', 'paid'
            elif r['paid'] > 0:
                status, css = 'Частично', 'partial'
            elif d and d < today:
                status, css = 'Просрочена', 'unpaid'
            else:
                status, css = 'Предстояща', 'future'
            installments_list.append({
                'num': r['installment_num'],
                'due_date': format_date(r['due_date']),
                'amount': format_num(r['amount']),
                'paid': format_num(r['paid']),
                'remaining': format_num(rem),
                'status': status, 'css': css
            })
    # Разбивка на задължението
    breakdown = None
    if cid:
        today_db = date.today().strftime('%Y-%m-%d')
        # Неплатени вноски
        inst_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM installments WHERE contract_id=? AND amount > paid", (cid,)).fetchone()[0]
        # Просрочени вноски (падеж < днес)
        inst_overdue = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM installments WHERE contract_id=? AND amount > paid AND due_date < ?", (cid, today_db)).fetchone()[0]
        # Предстоящи вноски (падеж >= днес)
        inst_upcoming = round(inst_owed - inst_overdue, 2)
        # Разходи по тип
        fines_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Глоба' AND amount > paid", (cid,)).fetchone()[0]
        insurance_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Застраховка' AND amount > paid", (cid,)).fetchone()[0]
        penalties_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Неустойка' AND amount > paid", (cid,)).fetchone()[0]
        other_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type NOT IN ('Глоба','Застраховка','Неустойка') AND amount > paid", (cid,)).fetchone()[0]
        # Начислени неустойки (0,5% дневно върху просрочени суми) - изчисляваме тук
        DAILY_RATE = 0.005
        accrued_penalty = 0.0
        overdue_inst = conn.execute("SELECT due_date, amount, paid FROM installments WHERE contract_id=? AND due_date < ? AND amount > paid", (cid, today_db)).fetchall()
        today_d = date.today()
        for oi in overdue_inst:
            d = parse_date(str(oi['due_date']))
            if d:
                days = (today_d - d).days
                principal = round(oi['amount'] - oi['paid'], 2)
                accrued_penalty += round(principal * DAILY_RATE * days, 2)
        overdue_exp = conn.execute("SELECT due_date, amount, paid FROM expenses WHERE contract_id=? AND due_date < ? AND amount > paid", (cid, today_db)).fetchall()
        for oe in overdue_exp:
            d = parse_date(str(oe['due_date']))
            if d:
                days = (today_d - d).days
                principal = round(oe['amount'] - oe['paid'], 2)
                accrued_penalty += round(principal * DAILY_RATE * days, 2)

        total_debt = round(inst_overdue + inst_upcoming + fines_owed + insurance_owed + penalties_owed + other_owed + accrued_penalty, 2)
        breakdown = {
            'inst_overdue': format_num(inst_overdue),
            'inst_upcoming': format_num(inst_upcoming),
            'fines': format_num(fines_owed),
            'insurance': format_num(insurance_owed),
            'penalties': format_num(penalties_owed),
            'accrued_penalty': format_num(accrued_penalty),
            'other': format_num(other_owed),
            'total': format_num(total_debt)
        }

    conn.close()
    return render_template('payments.html', contracts=contract_list, selected=cid,
                           payments=payments_list, installments=installments_list,
                           breakdown=breakdown)

@app.route('/payments/add', methods=['POST'])
def payment_add():
    cid = request.form.get('contract_id', '')
    if not cid:
        flash('Изберете договор.', 'danger')
        return redirect(url_for('payments'))
    d = parse_date(request.form.get('payment_date', ''))
    if not d:
        flash('Невалидна дата.', 'danger')
        return redirect(url_for('payments', contract_id=cid))
    amount = parse_number(request.form.get('amount', ''))
    if amount <= 0:
        flash('Въведете сума над нула.', 'danger')
        return redirect(url_for('payments', contract_id=cid))
    result = distribute_payment(cid, d.strftime('%Y-%m-%d'), amount, request.form.get('note', ''))
    msg = (f"Разпределение на {format_num(amount)} лв.: "
           f"Глоби: {format_num(result['for_fines'])} лв., "
           f"Застраховки: {format_num(result['for_insurance'])} лв., "
           f"Неустойки: {format_num(result['for_penalties'])} лв., "
           f"Други: {format_num(result['for_other'])} лв., "
           f"Просрочени: {format_num(result['for_overdue'])} лв., "
           f"Редовни: {format_num(result['for_regular'])} лв., "
           f"Неразпределено: {format_num(result['unallocated'])} лв.")
    flash(msg, 'info')
    return redirect(url_for('payments', contract_id=cid))

@app.route('/payments/delete/<int:payment_id>', methods=['POST'])
def payment_delete(payment_id):
    cid = request.form.get('contract_id', '')
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
    conn.commit()
    conn.close()
    flash('Плащането е изтрито. Внимание: разпределението по вноски НЕ е възстановено.', 'warning')
    return redirect(url_for('payments', contract_id=cid))

# ============================================
# МАРШРУТИ — НЕУСТОЙКИ
# ============================================
@app.route('/penalties')
def penalties():
    conn = get_conn()
    contract_list = get_contract_list(conn)
    cid = request.args.get('contract_id', '')
    calc_date_str = request.args.get('calc_date', today_str())
    calc_date = parse_date(calc_date_str) or date.today()
    penalties_list = []
    total_penalty = 0.0
    reminder_text = ''
    DAILY_RATE = 0.005

    if cid:
        inst_rows = conn.execute("""SELECT installment_num, due_date, amount, paid
                                    FROM installments WHERE contract_id=? AND due_date<? AND amount>paid
                                    ORDER BY due_date""",
                                 (cid, calc_date.strftime('%Y-%m-%d'))).fetchall()
        exp_rows = conn.execute("""SELECT description, expense_type, due_date, amount, paid
                                   FROM expenses WHERE contract_id=? AND due_date<? AND amount>paid
                                   ORDER BY due_date""",
                                (cid, calc_date.strftime('%Y-%m-%d'))).fetchall()

        for r in inst_rows:
            d = parse_date(str(r['due_date']))
            if not d: continue
            overdue = (calc_date - d).days
            principal = round(r['amount'] - r['paid'], 2)
            penalty = round(principal * DAILY_RATE * overdue, 2)
            total_owed = round(principal + penalty, 2)
            total_penalty += penalty
            penalties_list.append({
                'description': f'Вноска № {r["installment_num"]}',
                'due_date': format_date(d), 'principal': format_num(principal),
                'overdue_days': overdue, 'penalty_rate': '0,5%',
                'penalty': format_num(penalty), 'paid': format_num(r['paid']),
                'total_owed': format_num(total_owed), 'type': 'installment'
            })

        for r in exp_rows:
            d = parse_date(str(r['due_date']))
            if not d: continue
            overdue = (calc_date - d).days
            principal = round(r['amount'] - r['paid'], 2)
            penalty = round(principal * DAILY_RATE * overdue, 2)
            total_owed = round(principal + penalty, 2)
            total_penalty += penalty
            penalties_list.append({
                'description': f'{r["expense_type"]}: {r["description"] or ""}',
                'due_date': format_date(d), 'principal': format_num(principal),
                'overdue_days': overdue, 'penalty_rate': '0,5%',
                'penalty': format_num(penalty), 'paid': format_num(r['paid']),
                'total_owed': format_num(total_owed), 'type': 'expense'
            })

        # Напомнително писмо
        contract = conn.execute("SELECT client_name, reg_number, brand_model FROM contracts WHERE contract_id=?", (cid,)).fetchone()
        if contract and penalties_list:
            client = contract['client_name'] or ''
            reg = contract['reg_number'] or ''
            model = contract['brand_model'] or ''
            lines = []
            for p in penalties_list:
                lines.append(f"  {p['description']}: падеж {p['due_date']}, главница {p['principal']} лв., "
                             f"{p['overdue_days']} дни просрочие, неустойка {p['penalty']} лв., общо {p['total_owed']} лв.")
            reminder_text = f"""НАПОМНИТЕЛНО ПИСМО

Дата: {format_date(calc_date)}
До: {client}
Относно: Договор № {cid} — {model} ({reg})

Уважаеми/а {client},

Уведомяваме Ви, че към {format_date(calc_date)} имате следните просрочени задължения:

{chr(10).join(lines)}

Общо неустойки към {format_date(calc_date)}: {format_num(total_penalty)} лв.

Молим да погасите задълженията в най-кратък срок.

С уважение,
Управител"""

    conn.close()
    return render_template('penalties.html', contracts=contract_list, selected=cid,
                           calc_date=format_date(calc_date), calc_date_iso=calc_date.strftime('%Y-%m-%d'),
                           penalties=penalties_list,
                           total_penalty=format_num(total_penalty), reminder_text=reminder_text)

@app.route('/penalties/send_email', methods=['POST'])
def penalty_send_email():
    cid = request.form.get('contract_id', '')
    reminder_text = request.form.get('reminder_text', '')
    if not cid:
        flash('Изберете договор.', 'danger')
        return redirect(url_for('penalties'))
    conn = get_conn()
    client_email = conn.execute("SELECT email FROM contracts WHERE contract_id=?", (cid,)).fetchone()
    smtp = conn.execute("SELECT * FROM smtp_settings WHERE id=1").fetchone()
    conn.close()
    if not smtp:
        flash('Настройте SMTP в настройки.', 'danger')
        return redirect(url_for('penalties', contract_id=cid))
    if not client_email or not client_email['email']:
        flash('Договорът няма имейл на клиента.', 'danger')
        return redirect(url_for('penalties', contract_id=cid))
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{smtp['sender_name']} <{smtp['sender_email']}>"
        msg['To'] = client_email['email']
        msg['Subject'] = f'Напомнително писмо — Договор {cid}'
        msg.attach(MIMEText(reminder_text, 'plain', 'utf-8'))
        server = smtplib.SMTP(smtp['smtp_server'], smtp['smtp_port'])
        server.starttls()
        server.login(smtp['smtp_user'], smtp['smtp_password'])
        server.sendmail(smtp['sender_email'], client_email['email'], msg.as_string())
        server.quit()
        flash(f'Имейлът е изпратен до {client_email["email"]}.', 'success')
    except Exception as e:
        flash(f'Грешка при изпращане: {str(e)}', 'danger')
    return redirect(url_for('penalties', contract_id=cid))

# ============================================
# МАРШРУТИ — ОБОБЩЕНА СПРАВКА
# ============================================
@app.route('/summary')
def summary():
    conn = get_conn()
    contract_list = get_contract_list(conn)
    cid = request.args.get('contract_id', '')
    data = {'installments': [], 'expenses': [], 'payments': [],
            'total_inst_due': 0, 'total_inst_paid': 0,
            'total_exp_due': 0, 'total_exp_paid': 0,
            'total_paid': 0, 'total_due': 0, 'total_remaining': 0}

    as_of_str = request.args.get('as_of_date', '')
    as_of = parse_date(as_of_str) or date.today()
    as_of_date_iso = as_of.strftime('%Y-%m-%d')
    as_of_date_display = format_date(as_of)

    if cid:
        today = as_of  # използваме избраната дата навсякъде
        inst_rows = conn.execute("""SELECT installment_num, due_date, amount, paid
                                    FROM installments WHERE contract_id=? ORDER BY installment_num""", (cid,)).fetchall()
        for r in inst_rows:
            rem = round(r['amount'] - r['paid'], 2)
            d = parse_date(str(r['due_date']))
            if rem <= 0:
                status, css = 'Платена', 'paid'
            elif r['paid'] > 0:
                status, css = 'Частично', 'partial'
            elif d and d < today:
                status, css = 'ПРОСРОЧЕНА', 'unpaid'
            else:
                status, css = 'Предстояща', 'future'
            data['installments'].append({
                'num': r['installment_num'], 'due_date': format_date(r['due_date']),
                'amount': format_num(r['amount']), 'paid': format_num(r['paid']),
                'remaining': format_num(rem), 'status': status, 'css': css
            })
            data['total_inst_due'] += r['amount']
            data['total_inst_paid'] += r['paid']

        exp_rows = conn.execute("""SELECT expense_type, description, due_date, amount, paid
                                   FROM expenses WHERE contract_id=? ORDER BY due_date""", (cid,)).fetchall()
        for r in exp_rows:
            rem = round(r['amount'] - r['paid'], 2)
            css = 'paid' if rem <= 0 else ('partial' if r['paid'] > 0 else 'unpaid')
            data['expenses'].append({
                'type': r['expense_type'], 'description': r['description'] or '',
                'due_date': format_date(r['due_date']),
                'amount': format_num(r['amount']), 'paid': format_num(r['paid']),
                'remaining': format_num(rem), 'css': css
            })
            data['total_exp_due'] += r['amount']
            data['total_exp_paid'] += r['paid']

        pay_rows = conn.execute("""SELECT payment_date, amount, for_fines, for_insurance,
                                          for_penalties, for_other, for_overdue, for_regular, unallocated, note
                                   FROM payments WHERE contract_id=? ORDER BY payment_date""", (cid,)).fetchall()
        for r in pay_rows:
            data['payments'].append({
                'date': format_date(r['payment_date']),
                'amount': format_num(r['amount']),
                'for_fines': format_num(r['for_fines']),
                'for_insurance': format_num(r['for_insurance']),
                'for_penalties': format_num(r['for_penalties']),
                'for_other': format_num(r['for_other']),
                'for_overdue': format_num(r['for_overdue']),
                'for_regular': format_num(r['for_regular']),
                'unallocated': format_num(r['unallocated']),
                'note': r['note'] or ''
            })
            data['total_paid'] += r['amount']

        data['total_due'] = data['total_inst_due'] + data['total_exp_due']
        data['total_remaining'] = data['total_due'] - data['total_paid']

        # Разбивка на задължението
        today_db = today.strftime('%Y-%m-%d')
        inst_overdue = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM installments WHERE contract_id=? AND amount > paid AND due_date < ?", (cid, today_db)).fetchone()[0]
        inst_all_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM installments WHERE contract_id=? AND amount > paid", (cid,)).fetchone()[0]
        inst_upcoming = round(inst_all_owed - inst_overdue, 2)
        fines_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Глоба' AND amount > paid", (cid,)).fetchone()[0]
        insurance_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Застраховка' AND amount > paid", (cid,)).fetchone()[0]
        penalties_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type='Неустойка' AND amount > paid", (cid,)).fetchone()[0]
        other_owed = conn.execute("SELECT COALESCE(SUM(amount - paid), 0) FROM expenses WHERE contract_id=? AND expense_type NOT IN ('Глоба','Застраховка','Неустойка') AND amount > paid", (cid,)).fetchone()[0]
        # Начислени неустойки 0,5% дневно
        DAILY_RATE = 0.005
        accrued_penalty = 0.0
        overdue_inst = conn.execute("SELECT due_date, amount, paid FROM installments WHERE contract_id=? AND due_date < ? AND amount > paid", (cid, today_db)).fetchall()
        for oi in overdue_inst:
            d = parse_date(str(oi['due_date']))
            if d:
                days = (today - d).days
                principal = round(oi['amount'] - oi['paid'], 2)
                accrued_penalty += round(principal * DAILY_RATE * days, 2)
        overdue_exp = conn.execute("SELECT due_date, amount, paid FROM expenses WHERE contract_id=? AND due_date < ? AND amount > paid", (cid, today_db)).fetchall()
        for oe in overdue_exp:
            d = parse_date(str(oe['due_date']))
            if d:
                days = (today - d).days
                principal = round(oe['amount'] - oe['paid'], 2)
                accrued_penalty += round(principal * DAILY_RATE * days, 2)
        total_debt = round(inst_overdue + inst_upcoming + fines_owed + insurance_owed + penalties_owed + other_owed + accrued_penalty, 2)
        data['breakdown'] = {
            'inst_overdue': format_num(inst_overdue),
            'inst_upcoming': format_num(inst_upcoming),
            'fines': format_num(fines_owed),
            'insurance': format_num(insurance_owed),
            'penalties': format_num(penalties_owed),
            'accrued_penalty': format_num(accrued_penalty),
            'other': format_num(other_owed),
            'total': format_num(total_debt)
        }

        # Форматираме тоталите
        for key in ['total_inst_due', 'total_inst_paid', 'total_exp_due', 'total_exp_paid',
                     'total_paid', 'total_due', 'total_remaining']:
            data[key] = format_num(data[key])

    conn.close()
    return render_template('summary.html', contracts=contract_list, selected=cid, data=data,
                           as_of_date_iso=as_of_date_iso, as_of_date_display=as_of_date_display)

# ============================================
# МАРШРУТИ — СПРАВКА ПО ДОГОВОРИ
# ============================================
@app.route('/report')
def report():
    conn = get_conn()
    rep_date_str = request.args.get('date', today_str())
    rep_date = parse_date(rep_date_str) or date.today()
    rep_date_db = rep_date.strftime('%Y-%m-%d')
    selected_contracts = request.args.getlist('sel')

    all_contracts = conn.execute("SELECT contract_id, reg_number, brand_model, client_name, total_amount FROM contracts ORDER BY contract_id").fetchall()
    report_data = []
    totals = {'inst_due': 0, 'inst_paid': 0, 'exp_due': 0, 'exp_paid': 0, 'total_paid': 0, 'remaining': 0}

    for c in all_contracts:
        # Ако има избрани договори, показваме само тях
        if selected_contracts and c['contract_id'] not in selected_contracts:
            continue

        # Вноски с падеж до избраната дата
        inst = conn.execute("SELECT SUM(amount), SUM(paid) FROM installments WHERE contract_id=? AND due_date<=?",
                            (c['contract_id'], rep_date_db)).fetchone()
        # Разходи с падеж до избраната дата
        exp = conn.execute("SELECT SUM(amount), SUM(paid) FROM expenses WHERE contract_id=? AND due_date<=?",
                           (c['contract_id'], rep_date_db)).fetchone()
        # Плащания до избраната дата
        pay = conn.execute("SELECT SUM(amount) FROM payments WHERE contract_id=? AND payment_date<=?",
                           (c['contract_id'], rep_date_db)).fetchone()
        inst_due = inst[0] or 0
        inst_paid = inst[1] or 0
        exp_due = exp[0] or 0
        exp_paid = exp[1] or 0
        total_paid = pay[0] or 0
        remaining = (inst_due + exp_due) - total_paid

        report_data.append({
            'contract_id': c['contract_id'],
            'reg_number': c['reg_number'] or '',
            'brand_model': c['brand_model'] or '',
            'client_name': c['client_name'] or '',
            'total_amount': format_num(c['total_amount']),
            'inst_due': format_num(inst_due), 'inst_paid': format_num(inst_paid),
            'exp_due': format_num(exp_due), 'exp_paid': format_num(exp_paid),
            'total_paid': format_num(total_paid), 'remaining': format_num(remaining)
        })
        totals['inst_due'] += inst_due
        totals['inst_paid'] += inst_paid
        totals['exp_due'] += exp_due
        totals['exp_paid'] += exp_paid
        totals['total_paid'] += total_paid
        totals['remaining'] += remaining

    conn.close()
    for k in totals:
        totals[k] = format_num(totals[k])
    return render_template('report.html', report=report_data, totals=totals, date=format_date(rep_date),
                           date_iso=rep_date.strftime('%Y-%m-%d'),
                           all_contracts=all_contracts, selected_contracts=selected_contracts)

@app.route('/report/export')
def report_export():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
    except ImportError:
        flash('openpyxl не е инсталиран.', 'danger')
        return redirect(url_for('report'))

    rep_date_str = request.args.get('date', today_str())
    rep_date = parse_date(rep_date_str) or date.today()
    rep_date_db = rep_date.strftime('%Y-%m-%d')

    selected_contracts = request.args.getlist('sel')

    conn = get_conn()
    all_contracts = conn.execute("SELECT contract_id, reg_number, brand_model, client_name, total_amount FROM contracts ORDER BY contract_id").fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = 'Справка по договори'
    ws.append([f'Справка към дата: {format_date(rep_date)}'])
    ws.append([])
    headers = ['Договор', 'Рег. №', 'Марка/Модел', 'Наемател', 'Обща стойност',
               'Вноски дълж.', 'Вноски платени', 'Разходи дълж.',
               'Разходи платени', 'Общо платено', 'Остатък']
    ws.append(headers)
    for cell in ws[3]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    for c in all_contracts:
        if selected_contracts and c['contract_id'] not in selected_contracts:
            continue
        inst = conn.execute("SELECT SUM(amount), SUM(paid) FROM installments WHERE contract_id=? AND due_date<=?",
                            (c['contract_id'], rep_date_db)).fetchone()
        exp = conn.execute("SELECT SUM(amount), SUM(paid) FROM expenses WHERE contract_id=? AND due_date<=?",
                           (c['contract_id'], rep_date_db)).fetchone()
        pay = conn.execute("SELECT SUM(amount) FROM payments WHERE contract_id=? AND payment_date<=?",
                           (c['contract_id'], rep_date_db)).fetchone()
        inst_due = inst[0] or 0
        inst_paid = inst[1] or 0
        exp_due = exp[0] or 0
        exp_paid = exp[1] or 0
        total_paid = pay[0] or 0
        remaining = (inst_due + exp_due) - total_paid
        ws.append([c['contract_id'], c['reg_number'] or '', c['brand_model'] or '',
                   c['client_name'] or '', c['total_amount'],
                   inst_due, inst_paid, exp_due, exp_paid, total_paid, remaining])

    conn.close()
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True,
                     download_name=f'Справка_договори_{date.today().strftime("%Y%m%d")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ============================================
# МАРШРУТИ — SMTP НАСТРОЙКИ
# ============================================
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    conn = get_conn()
    if request.method == 'POST':
        v = {k: request.form.get(k, '').strip() for k in [
            'smtp_server', 'smtp_port', 'smtp_user', 'smtp_password', 'sender_name', 'sender_email'
        ]}
        conn.execute("DELETE FROM smtp_settings WHERE id=1")
        conn.execute("""INSERT INTO smtp_settings (id, smtp_server, smtp_port, smtp_user, smtp_password, sender_name, sender_email)
                        VALUES (1,?,?,?,?,?,?)""",
                     (v['smtp_server'], int(v['smtp_port'] or 587), v['smtp_user'],
                      v['smtp_password'], v['sender_name'], v['sender_email']))
        conn.commit()
        flash('SMTP настройките са запазени.', 'success')
        conn.close()
        return redirect(url_for('settings'))
    smtp = conn.execute("SELECT * FROM smtp_settings WHERE id=1").fetchone()
    conn.close()
    return render_template('settings.html', smtp=smtp)

# ============================================
# СТАРТИРАНЕ
# ============================================
if __name__ == '__main__':
    try:
        print()
        print('  ============================================')
        print('  AutoLeasing v2.0')
        print('  ============================================')
        print()
        print('  Initializing database...')
        init_db()
        print('  Database OK.')
        print()

        port = 5000
        import socket
        for p in range(5000, 5020):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(('127.0.0.1', p))
                s.close()
                port = p
                break
            except:
                continue

        print(f'  ============================================')
        print(f'  Server starting on port {port}...')
        print(f'')
        print(f'  Open your browser and go to:')
        print(f'  http://127.0.0.1:{port}')
        print(f'')
        print(f'  DO NOT CLOSE THIS WINDOW!')
        print(f'  ============================================')
        print()

        # Try to open browser automatically
        try:
            import webbrowser
            import threading
            threading.Timer(2.0, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
        except:
            pass

        app.run(host='0.0.0.0', port=port, debug=False)

    except Exception as e:
        print()
        print(f'  ERROR: {str(e)}')
        print()
        input('  Press Enter to exit...')

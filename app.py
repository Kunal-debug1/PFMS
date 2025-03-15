import smtplib
import re
from flask import Flask, render_template, redirect, url_for, flash, request, session
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from MySQLdb.cursors import DictCursor
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, email, Regexp
from twilio.rest import Client
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_mysqldb import MySQL
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, date
from flask_mail import Mail, Message
from datetime import datetime, timedelta
import MySQLdb
import os

# Flask app setup
app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'Pass@1234'  # Replace with the correct password
app.config['MYSQL_DB'] = 'finance_db'


mail = Mail(app)

mysql = MySQL(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# User model
class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email


@login_manager.user_loader
def load_user(id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM users WHERE id = %s', (id,))
    user = cursor.fetchone()
    if user:
        # Pass all required fields, including mobile_number
        return User(user['id'], user['username'], user['email'])
    return None

# Forms
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8),
        Regexp(r'(?=.*[A-Z])', message='Password must contain at least one uppercase letter'),
        Regexp(r'(?=.*[a-z])', message='Password must contain at least one lowercase letter'),
        Regexp(r'(?=.*\d)', message='Password must contain at least one digit'),
        Regexp(r'(?=.*[@$!%*?&])', message='Password must contain at least one special character')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
class TransactionForm(FlaskForm):
    date = StringField('Date', validators=[DataRequired()])
    category = StringField('Category', validators=[DataRequired()])
    amount = StringField('Amount', validators=[DataRequired()])
    description = StringField('Description', validators=[DataRequired()])
    type_of_transaction = StringField('Transaction Type (Income/Expense)', validators=[DataRequired()])
    submit = SubmitField('Add Transaction')

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE email = %s', (form.email.data,))
        user = cursor.fetchone()
        if user and bcrypt.check_password_hash(user['password_hash'], form.password.data):
            login_user(User(user['id'], user['username'], user['email']))
            return redirect(url_for('dashboard'))
        flash('Login failed. Check your email and password.', 'danger')
    return render_template('login.html', form=form)


@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()

    if form.validate_on_submit():
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        # Check if the email already exists in the database
        cursor.execute('SELECT * FROM users WHERE email = %s', (form.email.data,))
        user = cursor.fetchone()

        if user:
            flash('Email already exists. Please use a different email.', 'danger')
            return redirect(url_for('register'))

        # Hash the password
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')

        # Insert user data into the database (without mobile number)
        cursor.execute('INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)',
                       (form.username.data, form.email.data, hashed_password))
        mysql.connection.commit()

        # Flash a success message
        flash('Account created successfully! You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', form=form)


@app.route('/dashboard')
@login_required
def dashboard():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # If a balance is found, assign the amount, else set it to 0
    cursor.execute('SELECT * FROM balance WHERE user_id = %s', (current_user.id,))
    result = cursor.fetchone()
    balance_amount = result['amount'] if result else 0


    cursor.execute('SELECT * FROM bills WHERE user_id = %s', (current_user.id,))
    bills = cursor.fetchall()

    cursor.execute('SELECT * FROM transactions WHERE user_id = %s', (current_user.id,))
    transactions = cursor.fetchall()

    # Pass the data to the dashboard template
    return render_template('dashboard.html', username=current_user.username,
                           balance_amount=balance_amount, bills=bills, transactions=transactions)

# Home route
@app.route('/')
def home():
    return render_template('home.html')


# --------------------------------------------------------------------------------------------------------------------------
@app.route('/balance', methods=['GET', 'POST'])
@login_required
def balance():
    total_balance = 0.0
    accounts = []

    if request.method == 'POST':
        card_number = request.form.get('card_number')
        cardholder_name = request.form.get('cardholder_name')
        expiry_date = request.form.get('expiry_date')
        cvv = request.form.get('cvv')
        amount = request.form.get('amount')

        # Server-side validations
        errors = []
        if not card_number or not re.match(r'^\d{12}$', card_number):
            errors.append('Card number must be 12 digits.')
        if not cardholder_name or len(cardholder_name.strip()) < 3:
            errors.append('Cardholder name must be at least 3 characters long.')
        if not expiry_date or not re.match(r'^\d{4}-\d{2}-\d{2}$', expiry_date):
            errors.append('Expiry date must be in YYYY-MM-DD format.')
        if not cvv or not re.match(r'^\d{3}$', cvv):
            errors.append('CVV must be 3 digits.')
        if not amount or not re.match(r'^\d+(\.\d{1,2})?$', amount) or float(amount) <= 0:
            errors.append('Amount must be a positive number.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('balance'))

        try:
            # Insert account and update balance
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

            # Insert into accounts table
            insert_account_query = """
                 INSERT INTO accounts (user_id, card_number, cardholder_name, expiry_date, cvv, amount)
                 VALUES (%s, %s, %s, %s, %s, %s)
             """
            cursor.execute(insert_account_query,
                           (current_user.id, card_number, cardholder_name, expiry_date, cvv, float(amount)))

            # Update balance
            cursor.execute("SELECT * FROM balance WHERE user_id = %s", (current_user.id,))
            if cursor.fetchone() is None:
                cursor.execute("INSERT INTO balance (user_id, amount) VALUES (%s, %s)",
                               (current_user.id, float(amount)))
            else:
                cursor.execute('UPDATE balance SET amount = amount + %s WHERE user_id = %s',
                               (float(amount), current_user.id))

            # Commit changes
            mysql.connection.commit()
            flash('Account added successfully, and balance updated!', 'success')
        except Exception as e:
            mysql.connection.rollback()
            print(f"Error during database operation: {e}")
            flash(f'Error: {e}', 'danger')
        finally:
            cursor.close()

    try:
        # Fetch current balance and account details
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        # Fetch balance
        cursor.execute("SELECT amount FROM balance WHERE user_id = %s", (current_user.id,))
        result = cursor.fetchone()
        total_balance = result['amount'] if result else 0.0

        # Fetch accounts
        cursor.execute("""
             SELECT card_number, cardholder_name, expiry_date, cvv, amount
             FROM accounts
             WHERE user_id = %s
         """, (current_user.id,))
        accounts = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching data: {e}")
        flash(f'Error fetching data: {e}', 'danger')
    finally:
        cursor.close()

    # Render template
    return render_template('balance.html', accounts=accounts, balance_amount=total_balance,
                           username=current_user.username)


# --------------------------------------------------------------------------------------------------------------------------

@app.route('/transactions', methods=['GET', 'POST'])
@login_required
def transactions():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Ensure the user has a balance initialized
    cursor.execute('SELECT * FROM balance WHERE user_id = %s', (current_user.id,))
    balance = cursor.fetchone()

    if not balance:
        cursor.execute('INSERT INTO balance (user_id, amount) VALUES (%s, %s)', (current_user.id, 0))
        mysql.connection.commit()

    if request.method == 'POST':
        # Get the form data
        date = request.form['date']
        transaction_type = request.form['type']  # credit or debit
        amount = request.form['amount']
        category = request.form['category']
        description = request.form['description']

        # Validations
        errors = []

        # Date Validation (Ensure the date is in the correct format)
        if not date or not re.match(r'\d{4}-\d{2}-\d{2}', date):  # Format YYYY-MM-DD
            errors.append('Invalid date format. Please use YYYY-MM-DD.')

        # Transaction Type Validation (Must be 'credit' or 'debit')
        if transaction_type not in ['credit', 'debit']:
            errors.append('Invalid transaction type. It must be either "credit" or "debit".')

        # Amount Validation (Ensure it's a positive number)
        try:
            amount = float(amount)
            if amount <= 0:
                errors.append('Amount must be a positive number.')
        except ValueError:
            errors.append('Amount must be a valid number.')

        # Category and Description Validation (Ensure they are not empty)
        if not category or len(category.strip()) < 3:
            errors.append('Category must be at least 3 characters long.')
        if not description or len(description.strip()) < 3:
            errors.append('Description must be at least 3 characters long.')

        # If there are errors, flash them and redirect back to the transactions page
        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('transactions'))

        # Insert transaction into the database if no errors
        try:
            cursor.execute(''' 
                INSERT INTO transactions (user_id, date, type, amount, category, description)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (current_user.id, date, transaction_type, amount, category, description))
            mysql.connection.commit()

            # Update the balance based on transaction type
            if transaction_type == 'credit':
                # Add the amount to the balance
                cursor.execute('UPDATE balance SET amount = amount + %s WHERE user_id = %s', (amount, current_user.id))
            elif transaction_type == 'debit':
                # Deduct the amount from the balance
                cursor.execute('UPDATE balance SET amount = amount - %s WHERE user_id = %s', (amount, current_user.id))

            mysql.connection.commit()

            # After transaction, check if any goal is met
            check_goal_progress(current_user.id)

            flash('Transaction added successfully!', 'success')
        except Exception as e:
            mysql.connection.rollback()
            flash(f'Error: {e}', 'danger')

        # Redirect back to the transactions page
        return redirect(url_for('transactions'))

    # Fetch the transactions for the user
    cursor.execute('SELECT * FROM transactions WHERE user_id = %s', (current_user.id,))
    transactions = cursor.fetchall()

    return render_template('transactions.html', username=current_user.username, transactions=transactions)


@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    cursor = mysql.connection.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
    mysql.connection.commit()
    cursor.close()
    return redirect(url_for('transactions'))


@app.route('/transaction_chart')
def transaction_chart():
    # Prepare data for pie chart
    income = sum(t['amount'] for t in transactions if t['category'] == 'income')
    expense = sum(t['amount'] for t in transactions if t['category'] == 'expense')
    other = sum(t['amount'] for t in transactions if t['category'] == 'other')

    chart_data = {
        "labels": ['Income', 'Expense', 'Other'],
        "datasets": [{
            "data": [income, expense, other],
            "backgroundColor": ['#36A2EB', '#FF5733', '#FFBF00'],
            "hoverBackgroundColor": ['#2C3E50', '#C0392B', '#F39C12']
        }]
    }

    return render_template('expenses.html', chart_data=chart_data)




# --------------------------------------------------------------------------------------------------------------------------

@app.route('/expenses', methods=['GET', 'POST'])
def expenses():
    cursor = mysql.connection.cursor()

    # Fetching data for all types (expense, income, transfer) grouped by category for the current user
    cursor.execute("""
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE user_id = %s  -- Replace `user_id` with your column name for the user
        GROUP BY category
    """, (current_user.id,))  # Assuming you have `current_user.id` for the logged-in user
    data = cursor.fetchall()
    cursor.close()

    # Prepare data for the pie chart
    labels = [row[0] for row in data]  # Extract categories
    values = [row[1] for row in data]  # Extract total amounts

    # Fetching all transactions to display in the table (expenses only) for the current user
    cursor = mysql.connection.cursor()
    cursor.execute("""
        SELECT category, amount, date
        FROM transactions
        WHERE user_id = %s
    """, (current_user.id,))
    transactions = cursor.fetchall()
    cursor.close()

    # Pass data to the template
    return render_template(
        'expenses.html',
        username=current_user.username,
        labels=labels,
        values=values,
        transactions=transactions
    )

# --------------------------------------------------------------------------------------------------------------------------

@app.route('/goals', methods=['GET', 'POST'])
@login_required
def goals():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch goals for the current user
    cursor.execute("SELECT * FROM goals WHERE user_id = %s", (current_user.id,))
    goals = cursor.fetchall()

    # Handle goal addition
    if request.method == 'POST':
        goal = request.form['goal']
        target_amount = request.form.get('target_amount')
        current_amount = request.form.get('current_amount')
        due_date = request.form['due_date']

        if not goal or not target_amount or not current_amount or not due_date:
            flash('Please fill out all fields.', 'danger')
            return redirect(url_for('goals'))

        try:
            cursor.execute(
                "INSERT INTO goals (user_id, goal, target_amount, current_amount, due_date) VALUES (%s, %s, %s, %s, %s)",
                (current_user.id, goal, float(target_amount), float(current_amount), due_date))
            mysql.connection.commit()
            flash('Goal added successfully!', 'success')

        except MySQLdb.Error as e:
            flash(f"Error adding goal: {e}", 'danger')

        # Skip goal progress check after adding a new goal
        session['skip_goal_check'] = True
        return redirect(url_for('goals'))

    # Check goal progress
    if not session.get('skip_goal_check', False):
        achievement_messages = check_goal_progress(current_user.id)
        for message in achievement_messages:
            flash(message, 'success')

    # Reset session flag
    session.pop('skip_goal_check', None)

    return render_template('goals.html', username=current_user.username, goals=goals)


def check_goal_progress(user_id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch the user's goals
    cursor.execute("SELECT * FROM goals WHERE user_id = %s", (user_id,))
    goals = cursor.fetchall()

    # Fetch the current balance for the user
    cursor.execute("SELECT amount FROM balance WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    current_balance = result['amount'] if result else 0

    achievement_messages = []

    # Check if any goal has been reached
    for goal in goals:
        target_amount = goal['target_amount']
        if current_balance >= target_amount:
            # Goal reached or exceeded
            achievement_messages.append(f"Congratulations! You've reached your goal: {goal['goal']}!")
            cursor.execute('UPDATE goals SET status = "Completed" WHERE id = %s AND user_id = %s',
                           (goal['id'], user_id))
            mysql.connection.commit()

    cursor.close()
    return achievement_messages


@app.route('/delete_goal/<int:goal_id>', methods=['POST'])
@login_required
def delete_goal(goal_id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        # Delete the goal from the database
        cursor.execute('DELETE FROM goals WHERE id = %s AND user_id = %s', (goal_id, current_user.id))
        mysql.connection.commit()

        # Flash a success message
        flash('Goal deleted successfully!', 'success')
    except MySQLdb.Error as e:
        flash(f"Error deleting goal: {e}", 'danger')

    # Redirect back to the goals page
    return redirect(url_for('goals'))
# --------------------------------------------------------------------------------------------------------------------------


def send_email_alert(user_email, category, amount, due_date):
    sender_email = 'shrutisalve.exe@gmail.com'  # Replace with your Gmail account
    sender_password = 'ifqr vlyn odsn hinu'     # Replace with your app password for Gmail
    smtp_server = 'smtp.gmail.com'
    smtp_port = 587

    receiver_email = user_email

    # Email content
    subject = f"Reminder: Upcoming {category} Bill Payment Due Tomorrow"
    body = f"""
Dear {receiver_email.split('@')[0]},

We hope this message finds you well! This is a kind reminder that your {category} bill is due tomorrow. Below are the details of your upcoming payment:

-----------------------------------------------------------
Bill Details
-----------------------------------------------------------
Category   : {category}
Amount Due : Rs. {amount:.2f}
Due Date   : {due_date}
-----------------------------------------------------------

What You Need to Do:
Please ensure that the payment is completed before the due date to avoid late fees or service interruptions. Timely payments also help in maintaining a good credit history.

How to Pay:
- Online Banking / UPI: Use your preferred bank's net banking or apps like Google Pay or PhonePe.
- Credit / Debit Card Payments: Accessible via our online portal or mobile app.
- Authorized Collection Centers: Visit your nearest bill payment center.

Need Assistance?
If you have any questions or require further assistance, feel free to reach out to our customer service team. We're here to help!

Why It's Important:
Staying current with your payments avoids penalties, keeps your account in good standing, and ensures uninterrupted services.

Thank you for being a valued part of our community. We truly appreciate your cooperation.

Warm Regards,  
[PFMS-Personal Finanace Manager System]  
[Contact Information | 9308021312 | shrutisalve.exe@gmail.com]  

P.S. Paying your bill early or on time ensures a hassle-free experience!
    """

    try:
        # Create email message
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Send email via SMTP
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Secure the connection
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
            print(f"Email sent to {user_email} successfully!")
            return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"SMTP Authentication error: {e}")
        flash("Authentication failed. Please check your email credentials.", "danger")
    except smtplib.SMTPException as e:
        print(f"SMTP error: {e}")
        flash(f"Failed to send email: {e}", "danger")
    except Exception as e:
        print(f"Error sending email: {e}")
        flash(f"Failed to send email: {e}", "danger")
    return False


@app.route('/bills', methods=['GET', 'POST'])
@login_required
def bills():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch bills for the current user
    cursor.execute("SELECT * FROM bills WHERE user_id = %s", (current_user.id,))
    bills = cursor.fetchall()

    if request.method == 'POST':
        # Get form data
        date = request.form['date']
        category = request.form['category']
        amount = float(request.form['amount'])
        due_date = request.form['due_date']
        description = request.form['description']

        try:
            # Insert the bill into the database
            cursor.execute('''
                INSERT INTO bills (user_id, date, category, amount, due_date, description) 
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (current_user.id, date, category, amount, due_date, description))
            mysql.connection.commit()

            # Fetch the user's email
            cursor.execute('SELECT email FROM users WHERE id = %s', (current_user.id,))
            user = cursor.fetchone()
            user_email = user['email']

            if user_email:
                due_date_obj = datetime.strptime(due_date, '%Y-%m-%d')
                current_date = datetime.now()

                # Check if the due date is tomorrow
                if (due_date_obj - current_date).days == 1:
                    send_email_alert(user_email, category, amount, due_date)

            flash('Bill added successfully and reminder sent!', 'success')
        except MySQLdb.Error as e:
            flash(f"Error adding bill: {e}", 'danger')
            return redirect(url_for('bills'))

    return render_template('bills.html', username=current_user.username, bills=bills)


# Delete Bill Route
@app.route('/delete_bill/<int:bill_id>', methods=['POST'])
@login_required
def delete_bill(bill_id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        # Delete the bill from the database
        cursor.execute('DELETE FROM bills WHERE id = %s AND user_id = %s', (bill_id, current_user.id))
        mysql.connection.commit()
        flash('Bill deleted successfully!', 'success')
    except MySQLdb.Error as e:
        flash(f"Error deleting bill: {e}", 'danger')

    return redirect(url_for('bills'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


if __name__ == "__main__":
    app.run(debug=True)

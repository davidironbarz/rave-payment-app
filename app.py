import os
import json
import re
from flask import Flask, request, jsonify, render_template_string, redirect
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime
import random
import string
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash

load_dotenv()
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL')
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASS = os.getenv('EMAIL_PASS')

with open('config.json', 'r') as f:
    config = json.load(f)
MEMBER_EMAILS = config.get('member_emails', [])
MEMBER_PHONES = config.get('member_phones', [])

scope = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive'
]
creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
gs_client = gspread.authorize(creds)

# Initialize Google Sheets with error handling
try:
    sheet = gs_client.open_by_key(GOOGLE_SHEET_ID).sheet1
    GOOGLE_SHEETS_AVAILABLE = True
except Exception as e:
    print(f"Warning: Google Sheets not available: {e}")
    sheet = None
    GOOGLE_SHEETS_AVAILABLE = False

# Test mode - set to False to use local data file instead of Google Sheets
TEST_MODE = False  # Set to False to use local data file

# Local data file for offline operation
LOCAL_DATA_FILE = 'local_sales_data.json'

def load_local_data():
    """Load data from local JSON file"""
    try:
        with open(LOCAL_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_local_data(data):
    """Save data to local JSON file"""
    with open(LOCAL_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Initialize with local data if Google Sheets unavailable
if not GOOGLE_SHEETS_AVAILABLE:
    print("Google Sheets unavailable, using local data file")
    LOCAL_MODE = True
else:
    LOCAL_MODE = False

if TEST_MODE:
    # Mock data for testing
    MOCK_RECORDS = [
        {
            'Timestamp': '2025-07-23T10:00:00',
            'Buyer Name': 'John Smith',
            'Ticket Number': 'TKT001',
            'Buyer Contact': 'john@example.com',
            'Ticket/Table Type': 'Ticket',
            'Ticket or Table': 'Ticket',
            'Amount Paid': '100',
            'Member Name': 'David',
            'Proof of Payment (base64)': ''
        },
        {
            'Timestamp': '2025-07-23T11:00:00',
            'Buyer Name': 'Jane Doe',
            'Ticket Number': 'TBL001',
            'Buyer Contact': '+1234567890',
            'Ticket/Table Type': 'Gold',
            'Ticket or Table': 'Table',
            'Amount Paid': '2396',
            'Member Name': 'Carlito',
            'Proof of Payment (base64)': ''
        },
        {
            'Timestamp': '2025-07-23T12:00:00',
            'Buyer Name': 'Mike Johnson',
            'Ticket Number': 'TKT002',
            'Buyer Contact': 'mike@example.com',
            'Ticket/Table Type': 'Ticket',
            'Ticket or Table': 'Ticket',
            'Amount Paid': '100',
            'Member Name': 'Cass',
            'Proof of Payment (base64)': ''
        }
    ]

app = Flask(__name__)
app.secret_key = "theraveplusinternational2024incdonebyloverman"

TABLE_PRICES = {
    'Bronze B': 1050,
    'Bronze A': 1100,
    'Silver': 1490,
    'Gold': 2396,
    'Platinum B': 3346,
    'Platinum A': 4524
}
TICKET_PRICE = 100

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'

# User class for Flask-Login
class AdminUser(UserMixin):
    def __init__(self, username):
        self.id = username

# Load user callback
@login_manager.user_loader
def load_user(username):
    if username in config.get('admins', {}):
        return AdminUser(username)
    return None

# Admin login route
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        admin_hash = config.get('admins', {}).get(username)
        if admin_hash and check_password_hash(admin_hash, password):
            user = AdminUser(username)
            login_user(user)
            return render_template_string('''<script>window.location='/admin/dashboard';</script>''')
        else:
            return render_template_string(LOGIN_TEMPLATE, error='Invalid username or password')
    return render_template_string(LOGIN_TEMPLATE, error=None)

# Admin logout route
@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    # Redirect to main payment form (not admin login)
    return redirect('/')

# Simple dashboard route
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    # Fetch all records with proper fallback
    if LOCAL_MODE or not GOOGLE_SHEETS_AVAILABLE or sheet is None:
        # Use local data file
        records = load_local_data()
        print("Using local data file for dashboard")
    else:
        try:
            records = sheet.get_all_records()
        except Exception as e:
            print(f"Error accessing Google Sheets: {e}")
            records = load_local_data()  # Fallback to local data
    # Column indices (adjust if your sheet columns are different)
    # [Timestamp, Buyer Name, Ticket Number, Buyer Contact, Ticket/Table Type, Ticket or Table, Amount Paid, Member Name, Proof]
    ticket_count = 0
    table_count = 0
    ticket_revenue = 0
    table_revenue = 0
    for row in records:
        t_or_t = row.get('Ticket or Table', '').strip().lower()
        amt = float(row.get('Amount Paid', 0) or 0)
        if t_or_t == 'ticket':
            ticket_count += 1
            ticket_revenue += amt
        elif t_or_t == 'table':
            table_count += 1
            table_revenue += amt
    # Calculate leaderboard stats
    member_stats = {}
    for row in records:
        member = row.get('Member Name', 'Unknown')
        amount = float(row.get('Amount Paid', 0) or 0)
        if member not in member_stats:
            member_stats[member] = {'count': 0, 'revenue': 0}
        member_stats[member]['count'] += 1
        member_stats[member]['revenue'] += amount
    
    # Sort by revenue
    leaderboard = sorted(member_stats.items(), key=lambda x: x[1]['revenue'], reverse=True)
    
    # Render the enhanced dashboard
    return render_template_string('''
    <html>
    <head>
      <title>📊 Admin Dashboard - Rave Money Collection</title>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <style>
        * {
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }
        
        body { 
          font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          min-height: 100vh;
          padding: 20px;
        }
        
        .container { 
          max-width: 1200px; 
          margin: 0 auto; 
          background: rgba(255, 255, 255, 0.95);
          backdrop-filter: blur(10px);
          padding: 30px; 
          border-radius: 20px; 
          box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
          border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        h2, h3 { 
          text-align: center; 
          color: #2c3e50;
          font-weight: 700;
          text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        h2 {
          font-size: 2.2em;
          margin-bottom: 30px;
        }
        
        h3 {
          font-size: 1.5em;
          margin-bottom: 20px;
        }
        
        .stats { 
          display: flex; 
          justify-content: space-around; 
          margin-bottom: 30px; 
          flex-wrap: wrap; 
          gap: 15px;
        }
        
        .stat { 
          background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
          padding: 25px 30px; 
          border-radius: 15px; 
          font-size: 18px; 
          text-align: center; 
          box-shadow: 0 8px 25px rgba(0,0,0,0.1);
          border: 1px solid rgba(255, 255, 255, 0.2);
          min-width: 180px;
          transition: all 0.3s ease;
        }
        
        .stat:hover {
          transform: translateY(-5px);
          box-shadow: 0 12px 35px rgba(0,0,0,0.15);
        }
        
        .stat b {
          color: #667eea;
          font-size: 1.2em;
          font-weight: 700;
        }
        
        .controls { 
          display: flex; 
          gap: 15px; 
          margin-bottom: 30px; 
          flex-wrap: wrap; 
          align-items: center; 
        }
        
        .search-box { 
          flex: 1; 
          min-width: 200px; 
          padding: 12px 20px; 
          border: 2px solid #e8f4fd; 
          border-radius: 12px; 
          font-size: 16px;
          transition: all 0.3s ease;
        }
        
        .search-box:focus {
          outline: none;
          border-color: #667eea;
          box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .filter-select { 
          padding: 12px 20px; 
          border: 2px solid #e8f4fd; 
          border-radius: 12px; 
          font-size: 16px;
          transition: all 0.3s ease;
        }
        
        .filter-select:focus {
          outline: none;
          border-color: #667eea;
          box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .filter-select:invalid { color: #666; }
        .filter-select option[value=""] { color: #666; }
        #typeFilter { min-width: 200px; width: 200px; }
        #typeFilter option { min-width: 200px; width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        
        /* Choices.js specific styling for type filter */
        .choices[data-type*="select-one"] .choices__inner { min-width: 200px; }
        .choices[data-type*="select-one"] .choices__list--dropdown { min-width: 200px; }
        .choices[data-type*="select-one"] .choices__item { min-width: 200px; white-space: nowrap; }
        
        .export-btn { 
          background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); 
          color: white; 
          padding: 12px 20px; 
          border: none; 
          border-radius: 12px; 
          cursor: pointer; 
          font-weight: 600;
          font-size: 16px;
          transition: all 0.3s ease;
          box-shadow: 0 8px 25px rgba(39, 174, 96, 0.3);
        }
        
        .export-btn:hover { 
          transform: translateY(-3px);
          box-shadow: 0 12px 35px rgba(39, 174, 96, 0.4);
        }
        
        table { 
          width: 100%; 
          border-collapse: collapse; 
          margin-top: 20px; 
          background: white;
          border-radius: 15px;
          overflow: hidden;
          box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        th, td { 
          border: 1px solid #e8f4fd; 
          padding: 15px; 
          text-align: left; 
        }
        
        th { 
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
          color: #fff; 
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        
        tr:nth-child(even) { background: #f8f9fa; }
        tr:hover { background: #e8f4fd; transition: background 0.3s ease; }
        
        /* Mobile responsive table */
        @media (max-width: 768px) {
          .table-container {
            overflow-x: auto;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            background: white;
          }
          
          table {
            min-width: 800px;
            margin-top: 0;
            border-radius: 0;
            box-shadow: none;
          }
          
          th, td {
            padding: 10px 8px;
            font-size: 14px;
            white-space: nowrap;
          }
          
          .controls {
            flex-direction: column;
            gap: 10px;
          }
          
          .search-box, .filter-select, .export-btn {
            width: 100%;
            min-width: auto;
          }
          
          .stats {
            flex-direction: column;
            gap: 10px;
          }
          
          .stat {
            min-width: auto;
            width: 100%;
          }
          
          .container {
            padding: 20px 15px;
          }
          
          .nav-bar {
            flex-direction: column;
            gap: 15px;
            align-items: stretch;
          }
          
          .payment-form-link, .logout {
            text-align: center;
            width: 100%;
          }
        }
        
        .logout { 
          float: right; 
          background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
          color: white;
          padding: 12px 20px;
          text-decoration: none;
          border-radius: 12px;
          font-weight: 600;
          transition: all 0.3s ease;
          box-shadow: 0 8px 25px rgba(231, 76, 60, 0.3);
        }
        
        .logout:hover {
          transform: translateY(-3px);
          box-shadow: 0 12px 35px rgba(231, 76, 60, 0.4);
        }
        
        .nav-bar { 
          display: flex; 
          justify-content: space-between; 
          align-items: center; 
          margin-bottom: 30px; 
        }
        
        .payment-form-link { 
          background: linear-gradient(135deg, #2196F3 0%, #1976D2 100%); 
          color: white; 
          padding: 12px 20px; 
          text-decoration: none; 
          border-radius: 12px; 
          font-weight: 600; 
          transition: all 0.3s ease;
          box-shadow: 0 8px 25px rgba(33, 150, 243, 0.3);
        }
        
        .payment-form-link:hover { 
          transform: translateY(-3px);
          box-shadow: 0 12px 35px rgba(33, 150, 243, 0.4);
        }
        
        .leaderboard { 
          background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); 
          padding: 25px; 
          border-radius: 15px; 
          margin-bottom: 30px; 
          box-shadow: 0 8px 25px rgba(0,0,0,0.1);
          border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .leaderboard h3 { 
          margin-top: 0; 
          color: #2c3e50;
          font-size: 1.5em;
          font-weight: 700;
        }
        
        .leaderboard-item { 
          display: flex; 
          justify-content: space-between; 
          padding: 12px 0; 
          border-bottom: 1px solid #e8f4fd; 
          transition: all 0.3s ease;
        }
        
        .leaderboard-item:hover {
          background: rgba(102, 126, 234, 0.1);
          border-radius: 8px;
          padding-left: 10px;
          padding-right: 10px;
        }
        
        .leaderboard-item:last-child { border-bottom: none; }
        
        .leaderboard-item a {
          color: #667eea;
          text-decoration: none;
          font-weight: 600;
          transition: color 0.3s ease;
        }
        
        .leaderboard-item a:hover {
          color: #764ba2;
        }
        
        .hidden { display: none; }
        
        /* Choices.js custom styling */
        .choices__inner {
          background: #ffffff !important;
          border: 2px solid #e8f4fd !important;
          border-radius: 12px !important;
          padding: 12px 20px !important;
          min-height: auto !important;
        }
        
        .choices__list--dropdown {
          border: 2px solid #e8f4fd !important;
          border-radius: 12px !important;
          box-shadow: 0 10px 30px rgba(0,0,0,0.1) !important;
          margin-top: 5px !important;
        }
        
        .choices__item {
          padding: 12px 20px !important;
          border-radius: 8px !important;
          margin: 2px 0 !important;
        }
        
        .choices__item--selectable {
          transition: all 0.3s ease !important;
        }
        
        .choices__item--selectable:hover {
          background: #f8f9ff !important;
          transform: translateX(5px) !important;
        }
        
        .choices__item--selected {
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
          color: white !important;
        }
      </style>
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/public/assets/styles/choices.min.css" />
      <script src="https://cdn.jsdelivr.net/npm/choices.js/public/assets/scripts/choices.min.js"></script>
      <script>
      // Initialize Choices.js for dashboard dropdowns
      document.addEventListener('DOMContentLoaded', function() {
        new Choices('#memberFilter', {
          searchEnabled: true,
          searchPlaceholderValue: 'Search members...',
          placeholder: true,
          placeholderValue: 'All Members',
          removeItemButton: false,
          shouldSort: false,
          itemSelectText: ''
        });

        new Choices('#typeFilter', {
          searchEnabled: false,
          placeholder: true,
          placeholderValue: 'All Types',
          removeItemButton: false,
          shouldSort: false,
          itemSelectText: ''
        });
      });
      // Set placeholder text for dashboard dropdowns
      document.addEventListener('DOMContentLoaded', function() {
        const memberFilter = document.getElementById('memberFilter');
        const typeFilter = document.getElementById('typeFilter');
        
        // Add placeholder options
        memberFilter.innerHTML = '<option value="" selected disabled style="color: #666;">All Members</option>' + memberFilter.innerHTML;
        typeFilter.innerHTML = '<option value="" selected disabled style="color: #666;">All Types</option>' + typeFilter.innerHTML;
      });

      function filterTable() {
          const searchTerm = document.getElementById('searchBox').value.toLowerCase();
          const memberFilter = document.getElementById('memberFilter').value;
          const typeFilter = document.getElementById('typeFilter').value;
          const rows = document.querySelectorAll('#dataTable tbody tr');
          
          rows.forEach(row => {
              const buyerName = row.cells[1]?.textContent.toLowerCase() || '';
              const memberName = row.cells[7]?.textContent || '';
              const ticketType = row.cells[5]?.textContent || '';
              
              const matchesSearch = buyerName.includes(searchTerm);
              const matchesMember = !memberFilter || memberName === memberFilter;
              const matchesType = !typeFilter || ticketType === typeFilter;
              
              row.style.display = (matchesSearch && matchesMember && matchesType) ? '' : 'none';
          });
      }
      
      function exportCSV() {
          const table = document.getElementById('dataTable');
          const rows = Array.from(table.querySelectorAll('tr'));
          let csv = [];
          
          rows.forEach(row => {
              const cols = Array.from(row.querySelectorAll('th, td'));
              const rowData = cols.map(col => '"' + col.textContent.replace(/"/g, '""') + '"');
              csv.push(rowData.join(','));
          });
          
          const csvContent = csv.join('\\n');
          const blob = new Blob([csvContent], { type: 'text/csv' });
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'event_sales_' + new Date().toISOString().split('T')[0] + '.csv';
          a.click();
      }
      </script>
    </head>
    <body>
    <div class="container">
    <div class="nav-bar">
      <a href="/" class="payment-form-link">📝 Payment Form</a>
      <a href="/admin/logout" class="logout">🚪 Logout</a>
    </div>
    <h2>Welcome, {{ user }}! 👋</h2>
    
    <div class="stats">
      <div class="stat">🎫 Tickets Sold<br><b>{{ ticket_count }}</b></div>
      <div class="stat">🪑 Tables Sold<br><b>{{ table_count }}</b></div>
      <div class="stat">💰 Ticket Revenue<br><b>¥{{ ticket_revenue }}</b></div>
      <div class="stat">💵 Table Revenue<br><b>¥{{ table_revenue }}</b></div>
      <div class="stat">🏆 Total Revenue<br><b>¥{{ ticket_revenue + table_revenue }}</b></div>
    </div>
    
    <div class="leaderboard">
      <h3>🏆 Top Sellers Leaderboard</h3>
      {% for member, stats in leaderboard[:5] %}
      <div class="leaderboard-item">
        <span><strong><a href="/admin/member/{{ member }}">{{ member }}</a></strong></span>
        <span>{{ stats.count }} sales • ¥{{ stats.revenue }}</span>
      </div>
      {% endfor %}
    </div>
    
    <div class="controls">
      <input type="text" id="searchBox" class="search-box" placeholder="🔍 Search by buyer name..." onkeyup="filterTable()">
      <select id="memberFilter" class="filter-select" onchange="filterTable()">
        {% for member in members %}
        <option value="{{ member }}">{{ member }}</option>
        {% endfor %}
      </select>
      <select id="typeFilter" class="filter-select" onchange="filterTable()">
        <option value="Ticket">Tickets Only</option>
        <option value="Table">Tables Only</option>
      </select>
      <button onclick="exportCSV()" class="export-btn">📊 Export CSV</button>
    </div>
    
    <h3>📋 All Submissions</h3>
    <div class="table-container">
      <table id="dataTable">
        <thead>
          <tr>
            {% for col in columns %}<th>{{ col }}</th>{% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for row in records %}
          <tr>
            {% for col in columns %}<td>{{ row.get(col, '') }}</td>{% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    </div>
    </body>
    </html>
    ''',
    user=current_user.id,
    ticket_count=ticket_count,
    table_count=table_count,
    ticket_revenue=ticket_revenue,
    table_revenue=table_revenue,
    records=records,
    columns=["Timestamp", "Buyer Name", "Ticket Number", "Buyer Contact", "Ticket/Table Type", "Ticket or Table", "Amount Paid", "Member Name", "Notes", "Proof of Payment (base64)"],
    leaderboard=leaderboard,
    members=["David", "Smith", "Carlito", "Westbrook", "Gustavo", "DJ Walk", "Cass", "Jay", "Shadwin"]
    )

# Member-specific dashboard route
@app.route('/admin/member/<member_name>')
@login_required
def member_dashboard(member_name):
    # Fetch all records from Google Sheets
    records = sheet.get_all_records()
    
    # Filter records for this specific member
    member_records = [row for row in records if row.get('Member Name', '').strip() == member_name.strip()]
    
    # Calculate stats for this member
    ticket_count = 0
    table_count = 0
    ticket_revenue = 0
    table_revenue = 0
    
    for row in member_records:
        t_or_t = row.get('Ticket or Table', '').strip().lower()
        amt = float(row.get('Amount Paid', 0) or 0)
        if t_or_t == 'ticket':
            ticket_count += 1
            ticket_revenue += amt
        elif t_or_t == 'table':
            table_count += 1
            table_revenue += amt
    
    return render_template_string('''
    <html><head><title>{{ member_name }} Dashboard</title>
    <style>
    body { font-family: Arial; background: #f9f9f9; }
    .container { max-width: 1000px; margin: 30px auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px #ccc; }
    h2, h3 { text-align: center; }
    .stats { display: flex; justify-content: space-around; margin-bottom: 30px; flex-wrap: wrap; }
    .stat { background: #f5f5f5; padding: 18px 30px; border-radius: 8px; font-size: 18px; text-align: center; box-shadow: 0 1px 4px #eee; margin: 5px; }
    .controls { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }
    .search-box { flex: 1; min-width: 200px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
    .filter-select { padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
    .filter-select:invalid { color: #666; }
    .filter-select option[value=""] { color: #666; }
    #typeFilter { min-width: 200px; width: 200px; }
    #typeFilter option { min-width: 200px; width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    /* Choices.js specific styling for type filter */
    .choices[data-type*="select-one"] .choices__inner { min-width: 200px; }
    .choices[data-type*="select-one"] .choices__list--dropdown { min-width: 200px; }
    .choices[data-type*="select-one"] .choices__item { min-width: 200px; white-space: nowrap; }
    .export-btn { background: #4CAF50; color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; }
    .export-btn:hover { background: #45a049; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #4CAF50; color: #fff; }
    tr:nth-child(even) { background: #f2f2f2; }
    .logout { float: right; }
    .nav-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .payment-form-link { background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; transition: background 0.3s; }
    .payment-form-link:hover { background: #1976D2; }
    .leaderboard { background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 30px; }
    .leaderboard h3 { margin-top: 0; }
    .leaderboard-item { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; }
    .leaderboard-item:last-child { border-bottom: none; }
    .hidden { display: none; }
    </style>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/public/assets/styles/choices.min.css" />
    <script src="https://cdn.jsdelivr.net/npm/choices.js/public/assets/scripts/choices.min.js"></script>
    <script>
    // Initialize Choices.js for dashboard dropdowns
    document.addEventListener('DOMContentLoaded', function() {
      new Choices('#memberFilter', {
        searchEnabled: true,
        searchPlaceholderValue: 'Search members...',
        placeholder: true,
        placeholderValue: 'All Members',
        removeItemButton: false,
        shouldSort: false,
        itemSelectText: ''
      });

      new Choices('#typeFilter', {
        searchEnabled: false,
        placeholder: true,
        placeholderValue: 'All Types',
        removeItemButton: false,
        shouldSort: false,
        itemSelectText: ''
      });
    });
    // Set placeholder text for dashboard dropdowns
    document.addEventListener('DOMContentLoaded', function() {
      const memberFilter = document.getElementById('memberFilter');
      const typeFilter = document.getElementById('typeFilter');
      
      // Add placeholder options
      memberFilter.innerHTML = '<option value="" selected disabled style="color: #666;">All Members</option>' + memberFilter.innerHTML;
      typeFilter.innerHTML = '<option value="" selected disabled style="color: #666;">All Types</option>' + typeFilter.innerHTML;
    });

    function filterTable() {
        const searchTerm = document.getElementById('searchBox').value.toLowerCase();
        const memberFilter = document.getElementById('memberFilter').value;
        const typeFilter = document.getElementById('typeFilter').value;
        const rows = document.querySelectorAll('#dataTable tbody tr');
        
        rows.forEach(row => {
            const buyerName = row.cells[1]?.textContent.toLowerCase() || '';
            const memberName = row.cells[7]?.textContent || '';
            const ticketType = row.cells[5]?.textContent || '';
            
            const matchesSearch = buyerName.includes(searchTerm);
            const matchesMember = !memberFilter || memberName === memberFilter;
            const matchesType = !typeFilter || ticketType === typeFilter;
            
            row.style.display = (matchesSearch && matchesMember && matchesType) ? '' : 'none';
        });
    }
    
    function exportCSV() {
        const table = document.getElementById('dataTable');
        const rows = Array.from(table.querySelectorAll('tr'));
        let csv = [];
        
        rows.forEach(row => {
            const cols = Array.from(row.querySelectorAll('th, td'));
            const rowData = cols.map(col => '"' + col.textContent.replace(/"/g, '""') + '"');
            csv.push(rowData.join(','));
        });
        
        const csvContent = csv.join('\\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = '{{ member_name }}_sales_' + new Date().toISOString().split('T')[0] + '.csv';
        a.click();
    }
    </script>
    </head><body>
    <div class="container">
    <div class="nav-bar">
      <a href="/" class="payment-form-link">📝 Payment Form</a>
      <a href="/admin/logout" class="logout">🚪 Logout</a>
    </div>
    <h2>{{ member_name }}'s Sales Dashboard</h2>
    
    <div class="stats">
      <div class="stat">Tickets Sold<br><b>{{ ticket_count }}</b></div>
      <div class="stat">Tables Sold<br><b>{{ table_count }}</b></div>
      <div class="stat">Ticket Revenue<br><b>¥{{ ticket_revenue }}</b></div>
      <div class="stat">Table Revenue<br><b>¥{{ table_revenue }}</b></div>
      <div class="stat">Total Revenue<br><b>¥{{ ticket_revenue + table_revenue }}</b></div>
    </div>
    
    <div class="controls">
      <input type="text" id="searchBox" class="search-box" placeholder="Search by buyer name..." onkeyup="filterTable()">
      <select id="typeFilter" class="filter-select" onchange="filterTable()">
        <option value="Ticket">Tickets Only</option>
        <option value="Table">Tables Only</option>
      </select>
      <button onclick="exportCSV()" class="export-btn">📊 Export CSV</button>
    </div>
    
    <h3>{{ member_name }}'s Sales</h3>
    <table id="dataTable">
      <thead>
        <tr>
          {% for col in columns %}<th>{{ col }}</th>{% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in member_records %}
        <tr>
          {% for col in columns %}<td>{{ row.get(col, '') }}</td>{% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
    </body></html>
    ''',
    member_name=member_name,
    ticket_count=ticket_count,
    table_count=table_count,
    ticket_revenue=ticket_revenue,
    table_revenue=table_revenue,
    member_records=member_records,
    columns=["Timestamp", "Buyer Name", "Ticket Number", "Buyer Contact", "Ticket/Table Type", "Ticket or Table", "Amount Paid", "Member Name", "Proof of Payment (base64)"]
    )

# Login page template
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
  <title>🔐 Admin Login - Rave Money Collection</title>
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>��</text></svg>">
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    
    body { 
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    
    .login-container {
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(10px);
      border-radius: 20px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
      padding: 40px;
      max-width: 400px;
      width: 100%;
      border: 1px solid rgba(255, 255, 255, 0.2);
    }
    
    h2 { 
      text-align: center; 
      color: #2c3e50;
      font-size: 2.2em;
      font-weight: 700;
      margin-bottom: 30px;
      text-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    .form-group {
      margin-bottom: 25px;
      position: relative;
    }
    
    input { 
      width: 100%; 
      padding: 15px 20px; 
      border: 2px solid #e8f4fd;
      border-radius: 12px;
      font-size: 16px;
      transition: all 0.3s ease;
      background: #ffffff;
      color: #2c3e50;
      margin-bottom: 0;
    }
    
    input:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
      transform: translateY(-2px);
    }
    
    input::placeholder {
      color: #95a5a6;
      font-weight: 500;
    }
    
    button { 
      width: 100%; 
      padding: 18px 20px; 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white; 
      border: none; 
      border-radius: 12px;
      font-size: 18px; 
      font-weight: 600;
      cursor: pointer;
      text-transform: uppercase;
      letter-spacing: 1px;
      transition: all 0.3s ease;
      box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
    }
    
    button:hover {
      transform: translateY(-3px);
      box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
    }
    
    .error { 
      color: #e74c3c; 
      text-align: center; 
      margin-bottom: 20px;
      padding: 15px;
      background: #fdf2f2;
      border-radius: 10px;
      border-left: 4px solid #e74c3c;
      font-weight: 600;
    }
    
    .back-link {
      text-align: center;
      margin-top: 20px;
    }
    
    .back-link a {
      color: #667eea;
      text-decoration: none;
      font-weight: 600;
      transition: color 0.3s ease;
    }
    
    .back-link a:hover {
      color: #764ba2;
    }
    
    /* Mobile responsive improvements for login */
    @media (max-width: 768px) {
      body {
        padding: 15px;
      }
      
      .login-container {
        padding: 30px 25px;
        border-radius: 15px;
      }
      
      h2 {
        font-size: 1.8em;
        margin-bottom: 25px;
      }
      
      input {
        padding: 18px 20px;
        font-size: 18px; /* Better for mobile typing */
      }
      
      button {
        padding: 20px;
        font-size: 18px;
      }
      
      .error {
        padding: 20px;
        font-size: 16px;
        margin-bottom: 20px;
      }
    }
    
    @media (max-width: 480px) {
      .login-container {
        padding: 25px 20px;
      }
      
      h2 {
        font-size: 1.6em;
      }
      
      input, button {
        padding: 16px 18px;
        font-size: 16px;
      }
    }
  </style>
</head>
<body>
  <div class="login-container">
    <h2>🔐 Admin Login</h2>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post" autocomplete="off">
      <div class="form-group">
        <input type="text" name="username" placeholder="👤 Username" required autocomplete="new-username">
      </div>
      <div class="form-group">
        <input type="password" name="password" placeholder="🔒 Password" required autocomplete="new-password">
      </div>
      <button type="submit">🚀 Login</button>
    </form>
    <div class="back-link">
      <a href="/">← Back to Payment Form</a>
    </div>
  </div>
</body>
</html>
'''

def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def is_valid_phone(phone):
    return re.match(r"^\+?\d{7,15}$", phone)

def send_email(to_email, subject, content):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(content, 'html'))
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'Gmail SMTP error: {e}')
        return False

def send_sms(phone, message):
    if not N8N_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(N8N_WEBHOOK_URL, json={"phone": phone, "message": message})
        return resp.status_code == 200
    except Exception as e:
        print(f"n8n SMS error: {e}")
        return False

def get_sales_totals():
    records = sheet.get_all_records()
    ticket_count = 0
    ticket_total = 0
    table_total = 0
    for row in records:
        t_or_t = row.get('Ticket or Table', '').strip().lower()
        amt = float(row.get('Amount Paid', 0) or 0)
        if t_or_t == 'ticket':
            ticket_count += 1
            ticket_total += amt
        elif t_or_t == 'table':
            table_total += amt
    return ticket_count, ticket_total, table_total

def generate_ticket_code():
    return f'{random.randint(100, 999)}-{random.randint(100, 999)}-{random.randint(100, 999)}'

def generate_table_code():
    part1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
    part2 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f'TABLE-{part1}-{part2}'

@app.route('/')
def index():
    # Check if user is logged in
    is_logged_in = current_user.is_authenticated
    admin_link_text = "Dashboard" if is_logged_in else "Admin Login"
    admin_link_url = "/admin/dashboard" if is_logged_in else "/admin/login"
    
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🎫 Rave Money Collection - Payment System</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎫</text></svg>">
  <meta name="description" content="Professional payment recording and ticketing system for event management">
  <meta name="keywords" content="payment, ticketing, event management, money collection">
  <meta name="author" content="Rave Money Collection">
  <meta property="og:title" content="🎫 Rave Money Collection - Payment System">
  <meta property="og:description" content="Professional payment recording and ticketing system">
  <meta property="og:type" content="website">
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    
    body { 
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      padding: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    
    .main-container {
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(10px);
      border-radius: 20px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
      padding: 40px;
      max-width: 500px;
      width: 100%;
      position: relative;
      border: 1px solid rgba(255, 255, 255, 0.2);
    }
    
    h2 { 
      text-align: center; 
      color: #2c3e50;
      font-size: 2.2em;
      font-weight: 700;
      margin-bottom: 30px;
      text-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    .form-group { 
      margin-bottom: 25px; 
      position: relative;
    }
    
    label { 
      display: block; 
      margin-bottom: 8px; 
      color: #34495e;
      font-weight: 600;
      font-size: 0.95em;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    
    input, select, button { 
      width: 100%; 
      padding: 15px 20px; 
      box-sizing: border-box;
      border: 2px solid #e8f4fd;
      border-radius: 12px;
      font-size: 16px;
      transition: all 0.3s ease;
      background: #ffffff;
      color: #2c3e50;
    }
    
    input:focus, select:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
      transform: translateY(-2px);
    }
    
    button { 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white; 
      border: none; 
      font-size: 18px; 
      font-weight: 600;
      cursor: pointer;
      border-radius: 12px;
      padding: 18px 20px;
      text-transform: uppercase;
      letter-spacing: 1px;
      transition: all 0.3s ease;
      box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
    }
    
    button:hover {
      transform: translateY(-3px);
      box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
    }
    
    button:disabled { 
      background: linear-gradient(135deg, #bdc3c7 0%, #95a5a6 100%);
      transform: none;
      box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    .confirmation { 
      color: #27ae60; 
      margin-top: 20px; 
      text-align: center;
      padding: 15px;
      background: #d5f4e6;
      border-radius: 10px;
      border-left: 4px solid #27ae60;
      font-weight: 600;
    }
    
    .error { 
      color: #e74c3c; 
      margin-top: 20px; 
      text-align: center;
      padding: 15px;
      background: #fdf2f2;
      border-radius: 10px;
      border-left: 4px solid #e74c3c;
      font-weight: 600;
    }
    
    /* Admin link */
    .admin-link {
      position: fixed;
      top: 30px;
      right: 30px;
      background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
      color: white;
      padding: 15px 25px;
      border-radius: 30px;
      text-decoration: none;
      font-size: 14px;
      font-weight: 700;
      box-shadow: 0 8px 25px rgba(231, 76, 60, 0.3);
      transition: all 0.3s ease;
      z-index: 10000;
      border: 2px solid rgba(255, 255, 255, 0.2);
      min-width: 140px;
      text-align: center;
      display: inline-block;
      white-space: nowrap;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    
    .admin-link:hover {
      transform: translateY(-3px) scale(1.05);
      box-shadow: 0 12px 35px rgba(231, 76, 60, 0.4);
    }
    
    .dashboard-link {
      background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%) !important;
      box-shadow: 0 8px 25px rgba(39, 174, 96, 0.3) !important;
    }
    
    .dashboard-link:hover {
      box-shadow: 0 12px 35px rgba(39, 174, 96, 0.4) !important;
    }
    
    .admin-button {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 15px 30px;
      text-decoration: none;
      border-radius: 25px;
      font-weight: 700;
      display: inline-block;
      margin: 20px 0;
      text-transform: uppercase;
      letter-spacing: 1px;
      box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
      transition: all 0.3s ease;
    }
    
    .admin-button:hover {
      transform: translateY(-3px);
      box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
    }
    
    .admin-button.dashboard {
      background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
      box-shadow: 0 8px 25px rgba(39, 174, 96, 0.3);
    }
    
    .admin-button.dashboard:hover {
      box-shadow: 0 12px 35px rgba(39, 174, 96, 0.4);
    }
    
    .admin-button.login {
      background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
      box-shadow: 0 8px 25px rgba(231, 76, 60, 0.3);
    }
    
    .admin-button.login:hover {
      box-shadow: 0 12px 35px rgba(231, 76, 60, 0.4);
    }
    
    .spinner-overlay {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(255,255,255,0.9);
      backdrop-filter: blur(5px);
      z-index: 9999;
      justify-content: center;
      align-items: center;
    }
    
    .spinner {
      border: 6px solid #f3f3f3;
      border-top: 6px solid #667eea;
      border-radius: 50%;
      width: 60px;
      height: 60px;
      animation: spin 1s linear infinite;
      margin-bottom: 20px;
    }
    
    .spinner-text {
      color: #2c3e50;
      font-weight: 600;
      font-size: 18px;
    }
    
    .spinner-container {
      text-align: center;
      background: rgba(255, 255, 255, 0.95);
      padding: 40px;
      border-radius: 20px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.2);
    }
    
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    
    /* Choices.js custom styling */
    .choices__inner {
      background: #ffffff !important;
      border: 2px solid #e8f4fd !important;
      border-radius: 12px !important;
      padding: 15px 20px !important;
      min-height: auto !important;
    }
    
    .choices__list--dropdown {
      border: 2px solid #e8f4fd !important;
      border-radius: 12px !important;
      box-shadow: 0 10px 30px rgba(0,0,0,0.1) !important;
      margin-top: 5px !important;
    }
    
    .choices__item {
      padding: 12px 20px !important;
      border-radius: 8px !important;
      margin: 2px 0 !important;
    }
    
    .choices__item--selectable {
      transition: all 0.3s ease !important;
    }
    
    .choices__item--selectable:hover {
      background: #f8f9ff !important;
      transform: translateX(5px) !important;
    }
    
    .choices__item--selected {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
      color: white !important;
    }
    
    /* Mobile responsive improvements */
    @media (max-width: 768px) {
      body {
        padding: 10px;
      }
      
      .main-container {
        padding: 25px 20px;
        border-radius: 15px;
      }
      
      h2 {
        font-size: 1.8em;
        margin-bottom: 25px;
      }
      
      .form-group {
        margin-bottom: 20px;
      }
      
      input, select, button {
        padding: 18px 20px;
        font-size: 18px; /* Better for mobile typing */
      }
      
      button {
        padding: 20px;
        font-size: 18px;
        margin-top: 10px;
      }
      
      .admin-link {
        top: 15px;
        right: 15px;
        padding: 10px 15px;
        font-size: 12px;
        min-width: 100px;
      }
      
      .admin-button {
        padding: 15px 25px;
        font-size: 16px;
        margin: 15px 0;
      }
      
      /* Better mobile form spacing */
      .form-group label {
        font-size: 1em;
        margin-bottom: 8px;
      }
      
      /* Enhanced mobile touch targets */
      .choices__inner {
        padding: 18px 20px !important;
        min-height: auto !important;
      }
      
      .choices__item {
        padding: 15px 20px !important;
        font-size: 16px !important;
      }
      
      /* Better mobile confirmation/error messages */
      .confirmation, .error {
        padding: 20px;
        font-size: 16px;
        margin-top: 25px;
      }
      
      /* Mobile spinner improvements */
      .spinner-container {
        padding: 30px 25px;
      }
      
      .spinner {
        width: 50px;
        height: 50px;
      }
      
      .spinner-text {
        font-size: 16px;
      }
    }
    
    /* Extra small mobile devices */
    @media (max-width: 480px) {
      .main-container {
        padding: 20px 15px;
      }
      
      h2 {
        font-size: 1.6em;
      }
      
      input, select, button {
        padding: 16px 18px;
        font-size: 16px;
      }
      
      .admin-link {
        top: 10px;
        right: 10px;
        padding: 8px 12px;
        font-size: 11px;
        min-width: 90px;
      }
    }
  </style>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/public/assets/styles/choices.min.css" />
</head>
<body>
  <a href="{{ admin_link_url }}" class="admin-link {% if is_logged_in %}dashboard-link{% endif %}">{{ admin_link_text }}</a>
  
  <div class="main-container">
    <h2>🎫 Record Payment</h2>
    
    <div style="text-align: center; margin-bottom: 30px;">
      <a href="{{ admin_link_url }}" class="admin-button {% if is_logged_in %}dashboard{% else %}login{% endif %}">
        {% if is_logged_in %}📊 Dashboard{% else %}🔐 Admin Login{% endif %}
      </a>
    </div>
    
    <!-- QR codes temporarily hidden
    <div class="qr-codes">
      <div>
        <img src="/static/wechat_qr.jpg" alt="WeChat Pay QR">
        <div>WeChat Pay</div>
      </div>
      <div>
        <img src="/static/alipay_qr.jpg" alt="Alipay QR">
        <div>Alipay</div>
      </div>
    </div>
    -->
    
    <div class="spinner-overlay" id="spinnerOverlay">
      <div class="spinner-container">
        <div class="spinner"></div>
        <div class="spinner-text">Processing Payment...</div>
      </div>
    </div>
    
    <form id="paymentForm">
      <div class="form-group">
        <label for="buyerName">👤 Buyer Name</label>
        <input type="text" id="buyerName" required placeholder="Enter buyer's full name">
      </div>
      
      <div class="form-group">
        <label for="buyerContact">📞 Buyer Contact</label>
        <input type="text" id="buyerContact" required placeholder="Email or phone number">
      </div>
      
      <div class="form-group">
        <label for="ticketOrTable">🎟️ Ticket or Table</label>
        <select id="ticketOrTable" required>
          <option value="Ticket">Ticket</option>
          <option value="Table">Table</option>
        </select>
      </div>
      
      <div class="form-group">
        <label for="ticketType">🏷️ Table Type</label>
        <select id="ticketType" required>
          <option value="Bronze B">Bronze B</option>
          <option value="Bronze A">Bronze A</option>
          <option value="Silver">Silver</option>
          <option value="Gold">Gold</option>
          <option value="Platinum B">Platinum B</option>
          <option value="Platinum A">Platinum A</option>
        </select>
      </div>
      
      <div class="form-group">
        <label for="amountPaid">💰 Amount Paid</label>
        <input type="text" id="amountPaid" required placeholder="Enter amount">
      </div>
      
      <div class="form-group">
        <label for="memberName">👨‍💼 Member Name</label>
        <select id="memberName" required>
          <option value="David">David</option>
          <option value="Smith">Smith</option>
          <option value="Carlito">Carlito</option>
          <option value="Westbrook">Westbrook</option>
          <option value="Gustavo">Gustavo</option>
          <option value="DJ Walk">DJ Walk</option>
          <option value="Cass">Cass</option>
          <option value="Jay">Jay</option>
          <option value="Shadwin">Shadwin</option>
        </select>
      </div>
      
      <div class="form-group">
        <label for="notes">📝 Payment Notes (Optional)</label>
        <textarea id="notes" placeholder="Explain the payment details, special arrangements, or any additional information..." rows="3" style="width: 100%; padding: 15px 20px; border: 2px solid #e8f4fd; border-radius: 12px; font-size: 16px; transition: all 0.3s ease; background: #ffffff; color: #2c3e50; font-family: inherit; resize: vertical; min-height: 80px;"></textarea>
      </div>
      
      <button type="submit" id="submitBtn">🚀 Submit Payment</button>
    </form>
    
    <div id="confirmation" class="confirmation" style="display: none;"></div>
    <div id="error" class="error" style="display: none;"></div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/choices.js/public/assets/scripts/choices.min.js"></script>
  <script>
    // Initialize Choices.js for better dropdowns
    new Choices('#memberName', {
      searchEnabled: true,
      searchPlaceholderValue: 'Search members...',
      placeholder: true,
      placeholderValue: 'Select Member',
      removeItemButton: false,
      shouldSort: false,
      itemSelectText: '',
      noResultsText: 'No members found',
      noChoicesText: 'No members to choose from'
    });

    new Choices('#ticketType', {
      searchEnabled: true,
      searchPlaceholderValue: 'Search types...',
      placeholder: true,
      placeholderValue: 'Select Type',
      removeItemButton: false,
      shouldSort: false
    });

    new Choices('#ticketOrTable', {
      searchEnabled: false,
      placeholder: true,
      placeholderValue: 'Select',
      removeItemButton: false,
      shouldSort: false
    });

    // Auto-select ticket type when ticket is chosen and blur table options
    document.getElementById('ticketOrTable').addEventListener('change', function() {
      const ticketOrTable = this.value;
      const ticketTypeSelect = document.getElementById('ticketType');
      const amountPaidInput = document.getElementById('amountPaid');
      const ticketTypeChoices = ticketTypeSelect.choices;
      
      if (ticketOrTable === 'Ticket') {
        // Set ticket type to Ticket and amount to 100
        ticketTypeSelect.value = 'Ticket';
        amountPaidInput.value = '100';
        
        // Completely disable the entire select element
        ticketTypeSelect.disabled = true;
        ticketTypeSelect.style.opacity = '0.5';
        ticketTypeSelect.style.pointerEvents = 'none';
        ticketTypeSelect.style.cursor = 'not-allowed';
        ticketTypeSelect.style.backgroundColor = '#f5f5f5';
        
        // Also disable the Choices.js wrapper if it exists
        const choicesContainer = ticketTypeSelect.closest('.choices');
        if (choicesContainer) {
          choicesContainer.style.opacity = '0.5';
          choicesContainer.style.pointerEvents = 'none';
          choicesContainer.style.cursor = 'not-allowed';
        }
        
        // Prevent any clicks on the field
        ticketTypeSelect.onclick = function(e) {
          e.preventDefault();
          e.stopPropagation();
          return false;
        };
        
      } else if (ticketOrTable === 'Table') {
        // Clear values
        ticketTypeSelect.value = '';
        amountPaidInput.value = '';
        
        // Enable the select element
        ticketTypeSelect.disabled = false;
        ticketTypeSelect.style.opacity = '1';
        ticketTypeSelect.style.pointerEvents = 'auto';
        ticketTypeSelect.style.cursor = 'pointer';
        ticketTypeSelect.style.backgroundColor = '';
        
        // Enable the Choices.js wrapper if it exists
        const choicesContainer = ticketTypeSelect.closest('.choices');
        if (choicesContainer) {
          choicesContainer.style.opacity = '1';
          choicesContainer.style.pointerEvents = 'auto';
          choicesContainer.style.cursor = 'pointer';
        }
        
        // Remove click prevention
        ticketTypeSelect.onclick = null;
        
      } else {
        // Reset when nothing is selected
        ticketTypeSelect.value = '';
        amountPaidInput.value = '';
        
        // Enable the select element
        ticketTypeSelect.disabled = false;
        ticketTypeSelect.style.opacity = '1';
        ticketTypeSelect.style.pointerEvents = 'auto';
        ticketTypeSelect.style.cursor = 'pointer';
        ticketTypeSelect.style.backgroundColor = '';
        
        // Enable the Choices.js wrapper if it exists
        const choicesContainer = ticketTypeSelect.closest('.choices');
        if (choicesContainer) {
          choicesContainer.style.opacity = '1';
          choicesContainer.style.pointerEvents = 'auto';
          choicesContainer.style.cursor = 'pointer';
        }
        
        // Remove click prevention
        ticketTypeSelect.onclick = null;
      }
    });

    // Auto-fill amount when table type is selected
    document.getElementById('ticketType').addEventListener('change', function() {
      const ticketType = this.value;
      const amountPaidInput = document.getElementById('amountPaid');
      
      const prices = {
        'Bronze B': 1050,
        'Bronze A': 1100,
        'Silver': 1490,
        'Gold': 2396,
        'Platinum B': 3346,
        'Platinum A': 4524
      };
      
      if (prices[ticketType]) {
        amountPaidInput.value = prices[ticketType];
      }
    });

    document.getElementById('paymentForm').addEventListener('submit', async function(e) {
      e.preventDefault();
      
      const submitBtn = document.getElementById('submitBtn');
      const spinnerOverlay = document.getElementById('spinnerOverlay');
      const confirmation = document.getElementById('confirmation');
      const error = document.getElementById('error');
      
      // Show spinner and disable button
      spinnerOverlay.style.display = 'flex';
      submitBtn.disabled = true;
      confirmation.style.display = 'none';
      error.style.display = 'none';
      
      // Get form data
      const formData = new FormData();
      formData.append('buyerName', document.getElementById('buyerName').value);
      formData.append('buyerContact', document.getElementById('buyerContact').value);
      formData.append('ticketType', document.getElementById('ticketType').value);
      formData.append('ticketOrTable', document.getElementById('ticketOrTable').value);
      formData.append('amountPaid', document.getElementById('amountPaid').value);
      formData.append('memberName', document.getElementById('memberName').value);
      formData.append('notes', document.getElementById('notes').value);
      
      try {
        const response = await fetch('/submit', {
          method: 'POST',
          body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
          confirmation.textContent = result.message;
          confirmation.style.display = 'block';
          document.getElementById('paymentForm').reset();
          
          // Reset Choices.js dropdowns
          const memberChoice = document.querySelector('#memberName').choices;
          const ticketTypeChoice = document.querySelector('#ticketType').choices;
          const ticketOrTableChoice = document.querySelector('#ticketOrTable').choices;
          
          if (memberChoice) memberChoice.setChoiceByValue('');
          if (ticketTypeChoice) ticketTypeChoice.setChoiceByValue('');
          if (ticketOrTableChoice) ticketOrTableChoice.setChoiceByValue('');
          
          // Reset disabled state
          document.getElementById('ticketType').disabled = false;
          document.getElementById('ticketType').style.opacity = '1';
          document.getElementById('ticketType').style.pointerEvents = 'auto';
          if (ticketTypeChoice) ticketTypeChoice.enable();
          
        } else {
          error.textContent = result.message;
          error.style.display = 'block';
        }
      } catch (err) {
        error.textContent = 'An error occurred. Please try again.';
        error.style.display = 'block';
      } finally {
        spinnerOverlay.style.display = 'none';
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
''', is_logged_in=is_logged_in, admin_link_text=admin_link_text, admin_link_url=admin_link_url)

@app.route('/submit', methods=['POST'])
def submit():
    buyer_name = request.form.get('buyerName', '').strip()
    buyer_contact = request.form.get('buyerContact', '').strip()
    ticket_type = request.form.get('ticketType', '').strip()
    ticket_or_table = request.form.get('ticketOrTable', '').strip()
    amount_paid = request.form.get('amountPaid')
    member_name = request.form.get('memberName', '').strip()
    notes = request.form.get('notes', '').strip() # Get notes
    timestamp = datetime.now().isoformat()

    # Generate ticket/table code
    if ticket_or_table.lower() == 'ticket':
        new_code = generate_ticket_code()
    else:
        new_code = generate_table_code()

    if not buyer_name or not buyer_contact or not ticket_or_table or not amount_paid or not member_name:
        return jsonify({'success': False, 'message': 'Missing required fields.'}), 400

    is_email = is_valid_email(buyer_contact)
    is_phone = is_valid_phone(buyer_contact)
    if not (is_email or is_phone):
        return jsonify({'success': False, 'message': 'Buyer contact must be a valid email or phone number.'}), 400

    if ticket_or_table.lower() == 'ticket':
        if float(amount_paid) != TICKET_PRICE:
            return jsonify({'success': False, 'message': f'Ticket price must be {TICKET_PRICE} RMB.'}), 400
        ticket_type = 'Ticket'
    elif ticket_or_table.lower() == 'table':
        if ticket_type not in TABLE_PRICES:
            return jsonify({'success': False, 'message': 'Invalid table type.'}), 400
        if float(amount_paid) != TABLE_PRICES[ticket_type]:
            return jsonify({'success': False, 'message': f'{ticket_type} table price must be {TABLE_PRICES[ticket_type]} RMB.'}), 400
    else:
        return jsonify({'success': False, 'message': 'Ticket or Table must be specified.'}), 400

    # Create record for saving
    record = {
        'Timestamp': timestamp,
        'Buyer Name': buyer_name,
        'Ticket Number': new_code,
        'Buyer Contact': buyer_contact,
        'Ticket/Table Type': ticket_type,
        'Ticket or Table': ticket_or_table,
        'Amount Paid': amount_paid,
        'Member Name': member_name,
        'Notes': notes, # Add notes to record
        'Proof of Payment (base64)': ''
    }
    
    # Save to local file or Google Sheets
    if LOCAL_MODE or not GOOGLE_SHEETS_AVAILABLE:
        try:
            existing_data = load_local_data()
            existing_data.append(record)
            save_local_data(existing_data)
            print(f"Saved to local file: {buyer_name} - {ticket_or_table} {ticket_type}")
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error saving to local file: {e}'}), 500
    else:
        try:
            row = [timestamp, buyer_name, new_code, buyer_contact, ticket_type, ticket_or_table, amount_paid, member_name, notes, ''] # Add notes to row
            sheet.append_row(row)
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error saving to Google Sheets: {e}'}), 500

    ticket_count, ticket_total, table_total = get_sales_totals()

    # Update email content to include the new code
    code_type = "Ticket Number" if ticket_or_table.lower() == 'ticket' else "Table Code"
    buyer_msg = f"<p>Thank you for your payment!</p><p>Details:<br>Type: {ticket_or_table} {ticket_type}<br>Amount: {amount_paid} RMB<br>Member: {member_name}</p><h3>Your {code_type} is: {new_code}</h3><p>Please present this at the event for entry.</p>"
    sms_msg = f"Thank you for your payment! Your {code_type} is {new_code}. Member: {member_name}."
    if is_email:
        send_email(buyer_contact, "Your Event Ticket/Table Confirmation", buyer_msg)
    if is_phone:
        send_sms(buyer_contact, sms_msg)

    summary = f"A new sale was made!<br>Buyer: {buyer_name}<br>Type: {ticket_or_table} {ticket_type}<br>Amount: {amount_paid} RMB<br>Member: {member_name}<br><br>Totals:<br>Tickets sold: {ticket_count} (¥{ticket_total})<br>Table sales: ¥{table_total}"
    sms_summary = f"New sale! {ticket_or_table} {ticket_type}, {amount_paid} RMB. By {member_name}. Tickets: {ticket_count} (¥{ticket_total}), Tables: ¥{table_total}."
    for email in MEMBER_EMAILS:
        send_email(email, "Rave Sale Notification", summary)
    for phone in MEMBER_PHONES:
        send_sms(phone, sms_summary)

    return jsonify({'success': True, 'message': 'Payment recorded and notifications sent.'})

if __name__ == '__main__':
    app.run(debug=True) 
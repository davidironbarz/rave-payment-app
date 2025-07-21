import os
import json
import re
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime

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
sheet = gs_client.open_by_key(GOOGLE_SHEET_ID).sheet1

app = Flask(__name__)

TABLE_PRICES = {
    'Bronze B': 1050,
    'Bronze A': 1100,
    'Silver': 1490,
    'Gold': 2396,
    'Platinum B': 3346,
    'Platinum A': 4524
}
TICKET_PRICE = 100

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

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Record Payment</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 500px; margin: auto; padding: 20px; background: #f9f9f9; }
    h2 { text-align: center; }
    .form-group { margin-bottom: 15px; }
    label { display: block; margin-bottom: 5px; }
    input, select, button { width: 100%; padding: 8px; box-sizing: border-box; }
    button { background: #4CAF50; color: white; border: none; font-size: 16px; cursor: pointer; }
    button:disabled { background: #aaa; }
    .confirmation { color: green; margin-top: 10px; text-align: center; }
    .error { color: red; margin-top: 10px; text-align: center; }
    .qr-codes { display: flex; justify-content: space-around; margin-bottom: 20px; }
    .qr-codes img { width: 120px; border: 1px solid #ccc; background: #fff; }
    .qr-codes div { text-align: center; font-size: 14px; }
    .spinner-overlay {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(255,255,255,0.7);
      z-index: 9999;
      justify-content: center;
      align-items: center;
    }
    .spinner {
      border: 6px solid #f3f3f3;
      border-top: 6px solid #4CAF50;
      border-radius: 50%;
      width: 50px;
      height: 50px;
      animation: spin 1s linear infinite;
      margin-bottom: 10px;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
  </style>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/public/assets/styles/choices.min.css" />
</head>
<body>
  <h2>Record Payment</h2>
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
  <div class="spinner-overlay" id="spinnerOverlay">
    <div>
      <div class="spinner"></div>
      <div class="spinner-text">Submitting...</div>
    </div>
  </div>
  <form id="paymentForm">
    <div class="form-group">
      <label for="buyerName">Buyer Name</label>
      <input type="text" id="buyerName" required>
    </div>
    <div class="form-group">
      <label for="buyerContact">Buyer Contact (Email or Phone)</label>
      <input type="text" id="buyerContact" required>
    </div>
    <div class="form-group">
      <label for="ticketOrTable">Ticket or Table?</label>
      <select id="ticketOrTable" required>
        <option value="">Select</option>
        <option value="Ticket">Ticket</option>
        <option value="Table">Table</option>
      </select>
    </div>
    <div class="form-group" id="tableTypeGroup" style="display:none;">
      <label for="tableType">Table Type</label>
      <select id="tableType">
        <option value="">Select Table Type</option>
        <option value="Bronze B">Bronze B</option>
        <option value="Bronze A">Bronze A</option>
        <option value="Silver">Silver</option>
        <option value="Gold">Gold</option>
        <option value="Platinum B">Platinum B</option>
        <option value="Platinum A">Platinum A</option>
      </select>
    </div>
    <div class="form-group">
      <label for="amountPaid">Amount Paid (RMB)</label>
      <input type="number" id="amountPaid" required readonly>
    </div>
    <div class="form-group">
      <label for="memberName">Member Name</label>
      <select id="memberName" required>
        <option value="">Select Member</option>
        <option value="Carlito">Carlito</option>
        <option value="Cass">Cass</option>
        <option value="David">David</option>
        <option value="DJ Walk">DJ Walk</option>
        <option value="Gustavo">Gustavo</option>
        <option value="Jay">Jay</option>
        <option value="Shadwin">Shadwin</option>
        <option value="Smith">Smith</option>
        <option value="Westbrook">Westbrook</option>
      </select>
    </div>
    <div class="form-group">
      <label for="proof">Upload Proof of Payment (optional)</label>
      <input type="file" id="proof" accept="image/*">
    </div>
    <button type="submit">Submit</button>
  </form>
  <div class="confirmation" id="confirmationMsg" style="display:none;"></div>
  <div class="error" id="errorMsg" style="display:none;"></div>
  <script>
    const TABLE_PRICES = {
      'Bronze B': 1050,
      'Bronze A': 1100,
      'Silver': 1490,
      'Gold': 2396,
      'Platinum B': 3346,
      'Platinum A': 4524
    };
    const TICKET_PRICE = 100;

    document.getElementById('ticketOrTable').addEventListener('change', function() {
      const isTable = this.value === 'Table';
      document.getElementById('tableTypeGroup').style.display = isTable ? 'block' : 'none';
      document.getElementById('tableType').required = isTable;
      if (this.value === 'Ticket') {
        document.getElementById('amountPaid').value = TICKET_PRICE;
        document.getElementById('amountPaid').readOnly = true;
      } else if (this.value === 'Table') {
        const tableType = document.getElementById('tableType').value;
        document.getElementById('amountPaid').value = TABLE_PRICES[tableType] || '';
        document.getElementById('amountPaid').readOnly = true;
      } else {
        document.getElementById('amountPaid').value = '';
        document.getElementById('amountPaid').readOnly = true;
      }
    });
    document.getElementById('tableType').addEventListener('change', function() {
      const price = TABLE_PRICES[this.value] || '';
      document.getElementById('amountPaid').value = price;
    });
    document.addEventListener('DOMContentLoaded', function () {
      const memberSelect = document.getElementById('memberName');
      new Choices(memberSelect, {
        searchEnabled: true,
        itemSelectText: '',
        shouldSort: false
      });
    });
    const submitBtn = document.querySelector('button[type="submit"]');
    document.getElementById('paymentForm').onsubmit = async function(e) {
      e.preventDefault();
      submitBtn.disabled = true; // Disable button immediately
      document.getElementById('spinnerOverlay').style.display = 'flex';

      document.getElementById('confirmationMsg').style.display = 'none';
      document.getElementById('errorMsg').style.display = 'none';
      const buyer_name = document.getElementById('buyerName').value.trim();
      const buyer_contact = document.getElementById('buyerContact').value.trim();
      const ticket_or_table = document.getElementById('ticketOrTable').value;
      const ticket_table_type = ticket_or_table === 'Table' ? document.getElementById('tableType').value : '';
      const amount_paid = document.getElementById('amountPaid').value;
      const member_name = document.getElementById('memberName').value.trim();
      const proofFile = document.getElementById('proof').files[0];
      let proof_base64 = '';
      if (proofFile) {
        proof_base64 = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(proofFile);
        });
      }
      const payload = {
        buyer_name,
        buyer_contact,
        ticket_or_table,
        ticket_table_type,
        amount_paid,
        member_name,
        proof_base64
      };
      try {
        const res = await fetch('/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
          document.getElementById('confirmationMsg').textContent = data.message;
          document.getElementById('confirmationMsg').style.display = 'block';
          document.getElementById('paymentForm').reset();
          document.getElementById('amountPaid').value = '';
          document.getElementById('tableTypeGroup').style.display = 'none';
          setTimeout(() => { submitBtn.disabled = false; document.getElementById('spinnerOverlay').style.display = 'none'; }, 2000); // Re-enable after 2 seconds
        } else {
          document.getElementById('errorMsg').textContent = data.error || 'Submission failed.';
          document.getElementById('errorMsg').style.display = 'block';
          submitBtn.disabled = false; // Re-enable on error
          document.getElementById('spinnerOverlay').style.display = 'none';
        }
      } catch (err) {
        document.getElementById('errorMsg').textContent = 'Network or server error.';
        document.getElementById('errorMsg').style.display = 'block';
        submitBtn.disabled = false; // Re-enable on error
        document.getElementById('spinnerOverlay').style.display = 'none';
      }
    };
  </script>
</body>
</html>
''')

@app.route('/submit', methods=['POST'])
def submit():
    data = request.json
    buyer_name = data.get('buyer_name', '').strip()
    buyer_contact = data.get('buyer_contact', '').strip()
    ticket_or_table = data.get('ticket_or_table', '').strip()
    ticket_table_type = data.get('ticket_table_type', '').strip()
    amount_paid = data.get('amount_paid')
    member_name = data.get('member_name', '').strip()
    proof_base64 = data.get('proof_base64', '')
    timestamp = datetime.now().isoformat()

    if not buyer_name or not buyer_contact or not ticket_or_table or not amount_paid or not member_name:
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

    is_email = is_valid_email(buyer_contact)
    is_phone = is_valid_phone(buyer_contact)
    if not (is_email or is_phone):
        return jsonify({'success': False, 'error': 'Buyer contact must be a valid email or phone number.'}), 400

    if ticket_or_table.lower() == 'ticket':
        if float(amount_paid) != TICKET_PRICE:
            return jsonify({'success': False, 'error': f'Ticket price must be {TICKET_PRICE} RMB.'}), 400
        ticket_table_type = 'Ticket'
    elif ticket_or_table.lower() == 'table':
        if ticket_table_type not in TABLE_PRICES:
            return jsonify({'success': False, 'error': 'Invalid table type.'}), 400
        if float(amount_paid) != TABLE_PRICES[ticket_table_type]:
            return jsonify({'success': False, 'error': f'{ticket_table_type} table price must be {TABLE_PRICES[ticket_table_type]} RMB.'}), 400
    else:
        return jsonify({'success': False, 'error': 'Ticket or Table must be specified.'}), 400

    row = [timestamp, buyer_name, buyer_contact, ticket_table_type, ticket_or_table, amount_paid, member_name, proof_base64]
    try:
        sheet.append_row(row)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error saving to Google Sheets: {e}'}), 500

    ticket_count, ticket_total, table_total = get_sales_totals()

    buyer_msg = f"<p>Thank you for your payment!</p><p>Details:<br>Type: {ticket_or_table} {ticket_table_type}<br>Amount: {amount_paid} RMB<br>Member: {member_name}</p><p>Summer Chase 2.0 Awaits!!!</p><p>Happy Raving!!!</p>"
    sms_msg = f"Thank you for your payment! {ticket_or_table} {ticket_table_type}, {amount_paid} RMB. Member: {member_name}. Summer Chase 2.0 Awaits!!! Happy Raving!!!"
    if is_email:
        send_email(buyer_contact, "Payment Confirmation", buyer_msg)
    if is_phone:
        send_sms(buyer_contact, sms_msg)

    summary = f"A new sale was made!<br>Buyer: {buyer_name}<br>Type: {ticket_or_table} {ticket_table_type}<br>Amount: {amount_paid} RMB<br>Member: {member_name}<br><br>Totals:<br>Tickets sold: {ticket_count} (¥{ticket_total})<br>Table sales: ¥{table_total}"
    sms_summary = f"New sale! {ticket_or_table} {ticket_table_type}, {amount_paid} RMB. By {member_name}. Tickets: {ticket_count} (¥{ticket_total}), Tables: ¥{table_total}."
    for email in MEMBER_EMAILS:
        send_email(email, "Rave Sale Notification", summary)
    for phone in MEMBER_PHONES:
        send_sms(phone, sms_summary)

    return jsonify({'success': True, 'message': 'Payment recorded and notifications sent.'})

if __name__ == '__main__':
    app.run(debug=True) 
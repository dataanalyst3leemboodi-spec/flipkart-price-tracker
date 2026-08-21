import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import requests  # Render API Sync ke liye
import scraper

app = Flask(__name__)

# --- RENDER WEB APP URL ---
# Local scraping ke baad Render DB ko Sync karne ke liye endpoint URL
RENDER_SYNC_URL = "https://flipkart-price-tracker-y0hp.onrender.com/api/update_price"

# --- SQLITE DATABASE CONNECTION ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODELS ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fsn = db.Column(db.String(100), unique=True, nullable=False)
    title = db.Column(db.String(255), nullable=True)
    current_price = db.Column(db.Float, nullable=True)
    retailnet_price = db.Column(db.Float, nullable=True)
    siril_price = db.Column(db.Float, nullable=True)
    saara_price = db.Column(db.Float, nullable=True)
    petilante_price = db.Column(db.Float, nullable=True)
    optim_price = db.Column(db.Float, nullable=True)
    hsa_price = db.Column(db.Float, nullable=True)
    last_updated = db.Column(db.DateTime, default=datetime.now)
    last_auto_checked = db.Column(db.DateTime, nullable=True)
    history = db.relationship('PriceHistory', backref='product', lazy=True, cascade="all, delete-orphan")

class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    retailnet_price = db.Column(db.Float, nullable=True)
    siril_price = db.Column(db.Float, nullable=True)
    saara_price = db.Column(db.Float, nullable=True)
    petilante_price = db.Column(db.Float, nullable=True)
    optim_price = db.Column(db.Float, nullable=True)
    hsa_price = db.Column(db.Float, nullable=True)
    changed_at = db.Column(db.DateTime, default=datetime.now)

with app.app_context():
    db.create_all()

def parse_float(val):
    try:
        return float(val) if val not in [None, ""] else None
    except (ValueError, TypeError):
        return None

def save_or_update_seller_data(fsn, sellers_info, is_auto_scheduler=False):
    """Helper function to save/update database records from seller dict."""
    r_price = parse_float(sellers_info.get('RetailNet'))
    si_price = parse_float(sellers_info.get('Siril'))
    sa_price = parse_float(sellers_info.get('Saara'))
    pet_price = parse_float(sellers_info.get('PETILANTE Online'))
    opt_price = parse_float(sellers_info.get('OptimVRcommerce'))
    hsa_price = parse_float(sellers_info.get('HSAtlastradeFashion'))

    all_prices = [r_price, si_price, sa_price, pet_price, opt_price, hsa_price]
    main_price = r_price or next((v for v in all_prices if v is not None), None)

    prod = Product.query.filter_by(fsn=fsn).first()

    if not prod:
        prod = Product(
            fsn=fsn,
            title='Multi-Seller Tracking',
            current_price=main_price,
            retailnet_price=r_price,
            siril_price=si_price,
            saara_price=sa_price,
            petilante_price=pet_price,
            optim_price=opt_price,
            hsa_price=hsa_price,
            last_updated=datetime.now()
        )
        db.session.add(prod)
        db.session.flush()

        hist = PriceHistory(
            product_id=prod.id,
            retailnet_price=r_price,
            siril_price=si_price,
            saara_price=sa_price,
            petilante_price=pet_price,
            optim_price=opt_price,
            hsa_price=hsa_price,
            changed_at=datetime.now()
        )
        db.session.add(hist)
    else:
        has_changed = (
            prod.retailnet_price != r_price or
            prod.siril_price != si_price or
            prod.saara_price != sa_price or
            prod.petilante_price != pet_price or
            prod.optim_price != opt_price or
            prod.hsa_price != hsa_price
        )

        if has_changed:
            hist = PriceHistory(
                product_id=prod.id,
                retailnet_price=r_price,
                siril_price=si_price,
                saara_price=sa_price,
                petilante_price=pet_price,
                optim_price=opt_price,
                hsa_price=hsa_price,
                changed_at=datetime.now()
            )
            db.session.add(hist)

            prod.current_price = main_price
            prod.retailnet_price = r_price
            prod.siril_price = si_price
            prod.saara_price = sa_price
            prod.petilante_price = pet_price
            prod.optim_price = opt_price
            prod.hsa_price = hsa_price

        prod.last_updated = datetime.now()

    if is_auto_scheduler:
        prod.last_auto_checked = datetime.now()

    db.session.commit()

def sync_to_render(fsn, sellers_info):
    """Helper function to send live data from local system to Render server."""
    try:
        payload = {
            'fsn': fsn,
            'sellers': sellers_info
        }
        # Timeout rakha gaya hai taaki local scraping slow na ho agar Render thoda response me delay kare
        requests.post(RENDER_SYNC_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[SYNC ERROR] Could not push FSN {fsn} to Render: {e}")

def fetch_and_update_fsn(driver, fsn):
    sellers_info = scraper.extract_all_target_sellers(driver, fsn)
    
    # 1. Local Database update
    save_or_update_seller_data(fsn, sellers_info)
    
    # 2. Render Cloud Database sync
    sync_to_render(fsn, sellers_info)

# --- API ENDPOINT FOR SCRAPER CLOUD SYNC ---
@app.route('/api/update_price', methods=['POST'])
def api_update_price():
    try:
        data = request.get_json(force=True)
        fsn = data.get('fsn')
        sellers = data.get('sellers', {})
        
        if not fsn:
            return jsonify({'status': 'error', 'message': 'FSN missing'}), 400

        save_or_update_seller_data(fsn, sellers)
        return jsonify({'status': 'success', 'fsn': fsn}), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ROUTES ---
@app.route('/')
def index():
    search_query = request.args.get('search', '').strip()
    if search_query:
        products = Product.query.filter(Product.fsn.ilike(f'%{search_query}%')).all()
    else:
        products = Product.query.all()
        
    total_fsns = Product.query.count()
    return render_template('index.html', products=products, total_fsns=total_fsns, search_query=search_query)

# --- FILE UPLOAD ROUTE ---
@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        file = request.files.get('file')
        if not file:
            return jsonify({'error': 'No file uploaded'}), 400
            
        df = pd.read_excel(file) if file.filename.endswith('.xlsx') else pd.read_csv(file)
        fsn_col = [c for c in df.columns if 'fsn' in str(c).lower()]
        if not fsn_col:
            return jsonify({'error': "Excel me 'FSN' column nahi mila"}), 400
            
        fsn_list = df[fsn_col[0]].dropna().unique().tolist()
        
        driver = scraper.setup_driver()
        try:
            for fsn in fsn_list:
                fetch_and_update_fsn(driver, str(fsn).strip())
        finally:
            driver.quit()

        return jsonify({'status': 'success', 'message': f'{len(fsn_list)} FSNs Processed and Synced!'})
    except Exception as e:
        return jsonify({'error': f'Failed to process file: {str(e)}'}), 500

# --- ROUTE TO CLEAR ALL DATABASE DATA ---
@app.route('/clear-database', methods=['POST', 'GET'])
def clear_database():
    try:
        db.session.query(PriceHistory).delete()
        db.session.query(Product).delete()
        db.session.commit()
        return redirect(url_for('index'))
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to clear database: {str(e)}'}), 500

# Download Current Seller Wise Prices
@app.route('/download-seller-prices')
def download_seller_prices():
    products = Product.query.all()
    data = [{
        'FSN': p.fsn,
        'RetailNet Price': p.retailnet_price if p.retailnet_price is not None else '',
        'Siril Price': p.siril_price if p.siril_price is not None else '',
        'Saara Price': p.saara_price if p.saara_price is not None else '',
        'PETILANTE Online Price': p.petilante_price if p.petilante_price is not None else '',
        'OptimVRcommerce Price': p.optim_price if p.optim_price is not None else '',
        'HSAtlastradeFashion Price': p.hsa_price if p.hsa_price is not None else '',
        'Last Checked': p.last_updated.strftime('%Y-%m-%d %H:%M:%S') if p.last_updated else '',
        'Last Auto Check (4 PM)': p.last_auto_checked.strftime('%Y-%m-%d %H:%M:%S') if p.last_auto_checked else 'Not Checked Yet'
    } for p in products]

    cols = ['FSN', 'RetailNet Price', 'Siril Price', 'Saara Price', 'PETILANTE Online Price', 'OptimVRcommerce Price', 'HSAtlastradeFashion Price', 'Last Checked', 'Last Auto Check (4 PM)']
    df = pd.DataFrame(data, columns=cols) if data else pd.DataFrame(columns=cols)

    output_path = os.path.join('outputs', 'Seller_Wise_Prices.xlsx')
    os.makedirs('outputs', exist_ok=True)
    df.to_excel(output_path, index=False)
    return send_file(output_path, as_attachment=True)

# Download Price Change History
@app.route('/download-history')
def download_history():
    records = db.session.query(
        Product.fsn,
        PriceHistory.retailnet_price,
        PriceHistory.siril_price,
        PriceHistory.saara_price,
        PriceHistory.petilante_price,
        PriceHistory.optim_price,
        PriceHistory.hsa_price,
        PriceHistory.changed_at
    ).join(PriceHistory, Product.id == PriceHistory.product_id)\
     .order_by(PriceHistory.changed_at.asc()).all()

    data = [{
        'FSN': r.fsn,
        'RetailNet Price': r.retailnet_price if r.retailnet_price is not None else '',
        'Siril Price': r.siril_price if r.siril_price is not None else '',
        'Saara Price': r.saara_price if r.saara_price is not None else '',
        'PETILANTE Online Price': r.petilante_price if r.petilante_price is not None else '',
        'OptimVRcommerce Price': r.optim_price if r.optim_price is not None else '',
        'HSAtlastradeFashion Price': r.hsa_price if r.hsa_price is not None else '',
        'Changed On': r.changed_at.strftime('%Y-%m-%d %H:%M:%S')
    } for r in records]

    cols = ['FSN', 'RetailNet Price', 'Siril Price', 'Saara Price', 'PETILANTE Online Price', 'OptimVRcommerce Price', 'HSAtlastradeFashion Price', 'Changed On']
    df = pd.DataFrame(data, columns=cols) if data else pd.DataFrame(columns=cols)

    output_path = os.path.join('outputs', 'Price_Change_History.xlsx')
    os.makedirs('outputs', exist_ok=True)
    df.to_excel(output_path, index=False)
    return send_file(output_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

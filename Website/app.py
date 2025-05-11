from flask import Flask, render_template, request, jsonify
import pymysql
from pymysql.cursors import DictCursor
import logging
from functools import wraps

# Initialize flask
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Database configuration 
DB_CONFIG = {
    'host': 'sql-server-azure',
    'user': 'root',
    'password': 'ABCabc@23',
    'database': 'health',
    'cursorclass': DictCursor,
    'autocommit': True
}

def handle_database_errors(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except pymysql.Error as e:
            app.logger.error(f"Database error: {e}")
            return jsonify({'error': 'Database operation failed', 'details': str(e)}), 500
        except Exception as e:
            app.logger.error(f"Unexpected error: {e}")
            return jsonify({'error': 'An unexpected error occurred', 'details': str(e)}), 500
    return wrapper

def get_db_connection():
    """Create and return a new database connection with error handling"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        app.logger.info("Database connection established")
        return conn
    except pymysql.Error as e:
        app.logger.error(f"Database connection failed: {e}")
        raise RuntimeError(f"Database connection failed: {e}")

@app.route('/')
@handle_database_errors
def index():
    """Render the main search page with hospital and payer dropdowns"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT hospital_id, hospital_name FROM Hospital ORDER BY hospital_name")
            hospitals = cursor.fetchall()
            
            cursor.execute("SELECT DISTINCT payer_name, plan_name FROM Payer ORDER BY payer_name, plan_name")
            payers = cursor.fetchall()
            
        return render_template('index.html', hospitals=hospitals, payers=payers)
    finally:
        conn.close()

@app.route('/testdb')
@handle_database_errors
def test_db_connection():
    """Test endpoint to verify database connection"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 AS test_value")
            result = cursor.fetchone()
        return jsonify({
            'status': 'success',
            'database': DB_CONFIG['database'],
            'result': result
        })
    finally:
        conn.close()

@app.route('/search', methods=['POST'])
@handle_database_errors
def search():
    """Handle the search request and return charge information"""
    data = request.get_json() if request.is_json else request.form
    code = str(data.get('code', '')).strip()
    full_plan_name = data.get('plan_name', '')
    hospital_id = data.get('hospital_id', '')

    if not code:
        return jsonify({'error': 'Procedure code is required'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Verification if code exists
            cursor.execute("SELECT code_id FROM Code WHERE code_2 = %s", (code,))
            if not cursor.fetchone():
                return jsonify({'error': f'Procedure code {code} not found'}), 404

            #  best insurance
            best_insurance_query = """
                SELECT p.payer_name, p.plan_name,
                    MIN(COALESCE(
                        ch.standard_charge_negotiated_dollar, 
                        ch.standard_charge_gross * (1 - COALESCE(ch.standard_charge_negotiated_percentage, 0)/100)
                    )) AS best_rate
                FROM Charge ch
                JOIN Payer p ON ch.payer_id = p.payer_id
                JOIN Code c ON ch.code_id = c.code_id
                WHERE c.code_2 = %s
                GROUP BY p.payer_id
                ORDER BY best_rate ASC
                LIMIT 1
            """
            cursor.execute(best_insurance_query, [code])
            best_insurance = cursor.fetchone()

            #  best hospital 
            best_hospital_query = """
                SELECT h.hospital_name,
                    MIN(COALESCE(
                        ch.standard_charge_negotiated_dollar, 
                        ch.standard_charge_gross * (1 - COALESCE(ch.standard_charge_negotiated_percentage, 0)/100)
                    )) AS best_rate
                FROM Charge ch
                JOIN Hospital h ON ch.hospital_id = h.hospital_id
                JOIN Code c ON ch.code_id = c.code_id
                WHERE c.code_2 = %s
                GROUP BY h.hospital_id
                ORDER BY best_rate ASC
                LIMIT 1
            """
            cursor.execute(best_hospital_query, [code])
            best_hospital = cursor.fetchone()

            # specific search results if plan or hospital specified
            result = None
            if full_plan_name or hospital_id:
                query = """
                    SELECT h.hospital_name, p.payer_name, p.plan_name,
                        c.code_2 as procedure_code, c.description,
                        ch.standard_charge_gross, ch.standard_charge_discounted_cash,
                        COALESCE(
                            ch.standard_charge_negotiated_dollar, 
                            ch.standard_charge_gross * (1 - COALESCE(ch.standard_charge_negotiated_percentage, 0)/100)
                        ) AS negotiated_rate,
                        ch.standard_charge_min, ch.standard_charge_max
                    FROM Charge ch
                    JOIN Hospital h ON ch.hospital_id = h.hospital_id
                    JOIN Code c ON ch.code_id = c.code_id
                    JOIN Payer p ON ch.payer_id = p.payer_id
                    WHERE c.code_2 = %s
                """
                params = [code]

                if full_plan_name:
                    query += " AND p.plan_name = %s"
                    params.append(full_plan_name)

                if hospital_id:
                    query += " AND h.hospital_id = %s"
                    params.append(hospital_id)

                cursor.execute(query, params)
                result = cursor.fetchone()

            return jsonify({
                'data': result,
                'best_insurance': best_insurance,
                'best_hospital': best_hospital
            })
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)
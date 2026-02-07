import ast
import base64
import io
import os
import math
import datetime
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib

# Set backend to Agg to prevent GUI errors on server
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, jsonify, session, make_response
import pdfkit

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-me')
PATH_WKHTMLTOPDF = r'D:\wkhtmltopdf\bin\wkhtmltopdf.exe'
PDF_CONFIG = pdfkit.configuration(wkhtmltopdf=PATH_WKHTMLTOPDF)

# --- HELPER FUNCTIONS ---

def _normalize_number(s):
    """
    Cleans input strings, handling Persian/Arabic digits and commas.
    """
    if s is None: return None
    if isinstance(s, (int, float)): return s
    # Translate Persian/Arabic numerals to English
    s = str(s).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).replace(',', '.')
    try:
        return float(s)
    except:
        return None


def _to_float(v):
    """
    Safe float conversion.
    """
    if v is None: return 0.0
    try:
        return float(str(v).replace(',', '.'))
    except:
        return 0.0


def get_absolute_path(filename):
    root_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(root_dir, 'static', filename)


def parse_list(val):
    """
    Parses string representations of lists from Excel (e.g., "[10, 20, 30]")
    """
    if pd.isna(val) or val == '': return None
    try:
        return ast.literal_eval(str(val))
    except:
        return None


# --- CORE ROUTES ---

@app.route("/")
def welcome():
    session.clear()
    return render_template("welcome.html")


@app.route("/guide")
def guide():
    return render_template("guide.html")


# --- SIMPLE PATH ---

@app.route("/init_simple")
def init_simple():
    session.clear()
    session['mode'] = 'simple'  # Flag for Back Button logic
    # Reset pipe head to ensure step_cable knows we are in simple mode
    session['total_pipe_head'] = 0
    return redirect(url_for("simple_input"))


@app.route("/simple_input", methods=["GET", "POST"])
def simple_input():
    error_msg = None
    if request.method == "POST":
        flow = _normalize_number(request.form.get("flow"))
        head = _normalize_number(request.form.get("head"))

        if flow and head:
            if flow > 800:
                error_msg = "دبی بیش از حد مجاز است (حداکثر 800)."
            else:
                session.update({'flow': flow, 'final_q': flow, 'final_h': head})
                return redirect(url_for("match_page"))

    return render_template("simple_input.html", error_msg=error_msg)


# --- ADVANCED PATH ---

@app.route("/init_advanced")
def init_advanced():
    session.clear()
    session['mode'] = 'advanced'  # Flag for Back Button logic
    return redirect(url_for("step1"))


@app.route("/step1", methods=["GET", "POST"])
def step1():
    if request.method == "POST":
        raw_flow = _normalize_number(request.form.get("flow"))
        flow_unit = request.form.get("flow_unit")

        # Convert Lit/Sec to M3/H
        final_flow = raw_flow * 3.6 if flow_unit == 'lit_sec' and raw_flow else raw_flow

        session.update({
            'flow': final_flow,
            'static_level': _normalize_number(request.form.get("static_level")),
            'dynamic_level': _normalize_number(request.form.get("dynamic_level")),
            'install_depth': _normalize_number(request.form.get("install_depth")),
            'diameter': _normalize_number(request.form.get("diameter")) * 25.4  # Inch to mm
        })
        session['depth'] = session.get('dynamic_level')
        return redirect(url_for("step2"))

    return render_template("step1.html")


@app.route("/step2", methods=["GET", "POST"])
def step2():
    if request.method == "POST":
        segs = defaultdict(dict)
        for k, v in request.form.items():
            parts = k.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                segs[parts[1]][parts[0]] = v

        segments = []
        fittings_data = []  # Data specifically formatted for the Final Report
        total_pipe_head_loss = 0
        flow_m3h = session.get('flow', 0)
        q_m3s = flow_m3h / 3600.0 if flow_m3h else 0

        for idx in sorted(segs.keys(), key=int):
            s = segs[idx]
            L = _to_float(s.get('length') or 0)
            D_mm = _to_float(s.get('diameter') or 0)
            Z = _to_float(s.get('height') or 0)
            material_name = str(s.get('material', ''))

            # C Value selection logic
            C = 140 if "پلی" in material_name else 100

            # Hazen-Williams calculation
            row_hf = 0
            if L > 0 and D_mm > 0 and q_m3s > 0:
                D_m = D_mm / 1000.0
                row_hf = (10.67 * L * (q_m3s ** 1.852)) / ((C ** 1.852) * (D_m ** 4.871))

            row_total = row_hf + Z
            total_pipe_head_loss += row_total

            # For internal logic
            segments.append({
                'length': L,
                'diameter': D_mm,
                'height': Z,
                'material': material_name
            })

            # NEW: Data for Final Report (Advanced Mode)
            fittings_data.append({
                'name': material_name,
                'size': f"{D_mm} mm",
                'quantity': f"{L} m",
                'z_height': Z,
                'loss': round(row_hf, 2)
            })

        # Save to session
        session['segments'] = segments
        session['fittings_data'] = fittings_data  # Used in results.html
        session['total_pipe_head'] = total_pipe_head_loss

        # Save "Other" losses (Valves, etc)
        other_loss = _to_float(request.form.get("total_other_head"))
        session['total_other_head'] = other_loss

        # Add a record for other losses in the report data if it exists
        if other_loss > 0:
            session['fittings_data'].append({
                'name': 'سایر اتصالات و افت‌ها',
                'size': '-',
                'quantity': '-',
                'z_height': '-',
                'loss': round(other_loss, 2)
            })

        return redirect(url_for("step_design"))

    # GET Request: Load Pipe Data for Dropdowns
    file_path = 'data/pipe_Data.xlsx'
    materials_list = []
    try:
        if os.path.exists(file_path):
            df_mats = pd.read_excel(file_path, sheet_name='Sheet1')
            df_mats = df_mats.rename(columns={
                'Material': 'material_group',
                'Diameter': 'size_label',
                'ID': 'index',
                'Inner_D': 'inner_d',
                'C_Coef': 'C'
            })
            if 'hydraulic_d' not in df_mats.columns:
                df_mats['hydraulic_d'] = df_mats['inner_d']
            df_mats = df_mats.fillna(0)
            materials_list = df_mats.to_dict(orient='records')
    except Exception as e:
        print(f"Database Error: {e}")

    return render_template(
        "step2.html",
        materials=materials_list,
        flow=session.get('flow', 0),
        install_depth=session.get('install_depth', 0),
        segments=session.get('segments', [])
    )


@app.route("/step_design", methods=["GET", "POST"])
def step_design():
    # Only accessed in Advanced Mode
    flow = session.get("flow")
    if not flow:
        return redirect(url_for("welcome"))

    # Calculate Total Head
    pipe_head = _to_float(session.get("total_pipe_head") or 0)
    other_head = _to_float(session.get("total_other_head") or 0)
    total_h = pipe_head + other_head

    # Capture selected model if passed via URL (persistence)
    selected_model_name = request.args.get("model")
    if selected_model_name:
        session['pump_model'] = selected_model_name

    pump_details = {}
    # (Optional: fetch pump details here if needed for display on this page)

    if request.method == "POST":
        session.update({'final_q': flow, 'final_h': total_h})
        return redirect(url_for("match_page"))

    return render_template("step_design.html",
                           flow=flow,
                           head=round(total_h, 2),
                           # Back button goes to Fittings page
                           previous_step=url_for('step2'))


# --- MATCHING & API LOGIC ---

def get_best_pumps(qr, hr):
    file_path = 'data/cat_Data.xlsx'
    if not os.path.exists(file_path): return [], "فایل دیتابیس یافت نشد."

    try:
        df = pd.read_excel(file_path, header=1)  # Use header=1 based on previous context
        # Normalize column names
        df.columns = [str(c).strip().lower() for c in df.columns]
    except Exception as e:
        return [], f"خطا در خواندن اکسل: {str(e)}"

    results = []
    for _, row in df.iterrows():
        try:
            # Parse vectors using AST for safety
            f_vec = parse_list(row.get('flow_vector'))
            h_vec = parse_list(row.get('head_vector'))
            e_vec = parse_list(row.get('efficiency_vector'))

            if not f_vec or not h_vec: continue

            # Filter 1: Is flow within range?
            if not (min(f_vec) <= qr <= max(f_vec)): continue

            # Filter 2: Calculate Head at requested Flow
            # (Assuming polynomial coefficients exist in DB, or use Interpolation)
            h_coeffs = parse_list(row.get('head_flow_poly'))
            if not h_coeffs:
                # Fallback: Simple interpolation if no poly
                calc_h = np.interp(qr, f_vec, h_vec)
            else:
                h_func = np.poly1d(h_coeffs)
                calc_h = h_func(qr)

            # Tolerance check (e.g., pump head must be at least required head - 5m)
            if calc_h >= hr - 5:
                e_coeffs = parse_list(row.get('efficiency_flow_poly'))
                calc_eff = 0
                if e_coeffs:
                    e_func = np.poly1d(e_coeffs)
                    calc_eff = e_func(qr)

                results.append({
                    'name': str(row.get('shoowing_name', 'Unknown')),
                    'motor_power': _to_float(row.get('motor_power')),
                    'calc_head': round(calc_h, 2),
                    'calc_eff': round(calc_eff, 1),
                    'flow_vec': f_vec,
                    'head_vec': h_vec,
                    'eff_vec': e_vec,
                    # Store raw model for step_cable lookup
                    'raw_model': row.get('shoowing_name')
                })
        except Exception as e:
            continue

    if not results: return [], "موردی یافت نشد."
    # Sort: Lower Power first, then Higher Efficiency
    return sorted(results, key=lambda x: (x['motor_power'], -x['calc_eff']))[:6], None


@app.route("/match")
def match_page():
    if not session.get('final_q'): return redirect(url_for("welcome"))

    # NAVIGATION LOGIC: Where should the Back button go?
    if session.get('mode') == 'advanced':
        prev_url = url_for('step_design')
    else:
        prev_url = url_for('simple_input')  # or init_simple

    return render_template("match.html", previous_step=prev_url)


@app.route('/api/match')
def api_match():
    """
    API Endpoint used by JavaScript in match.html to fetch charts
    """
    qr = _to_float(session.get('final_q'))
    hr = _to_float(session.get('final_h'))

    pumps, error = get_best_pumps(qr, hr)
    if error: return jsonify({"error": error})

    formatted = []
    for p in pumps:
        # Create points for Chart.js
        h_pts = [{"x": f, "y": h} for f, h in zip(p['flow_vec'], p['head_vec'])]
        e_pts = [{"x": f, "y": e} for f, e in zip(p['flow_vec'], p['eff_vec'])]

        formatted.append({
            "model": p['name'],
            "power": p['motor_power'],
            "predicted_head": p['predicted_head'] if 'predicted_head' in p else p['calc_head'],
            "efficiency": p['efficiency'] if 'efficiency' in p else p['calc_eff'],
            "h_points": h_pts,
            "e_points": e_pts,
            "q_min": min(p['flow_vec']) - 5,
            "q_max": max(p['flow_vec']) + 5
        })

    return jsonify({"flow": qr, "required_head": hr, "candidates": formatted})


# --- CABLE CALCULATION (CRITICAL FIXES) ---

@app.route("/step_cable", methods=["GET", "POST"])
def step_cable():
    # 1. Capture model from URL or Session
    selected_model = request.args.get("model") or session.get("pump_model")
    if selected_model:
        session['pump_model'] = selected_model

    # 2. Fetch Pump Details
    pump_details = {}
    try:
        df_cat = pd.read_excel('data/cat_Data.xlsx', header=1)
        df_cat['Shoowing_name'] = df_cat['Shoowing_name'].astype(str).str.strip()
        match = df_cat[df_cat['Shoowing_name'] == str(selected_model).strip()]

        if not match.empty:
            row = match.iloc[0]
            pump_details = {
                'showing_name': row.get('Shoowing_name'),
                'motor_power': row.get('Motor_Power'),
                'current': _to_float(row.get('current')),
                'pump_model': int(row.get('Pump_Model')) if pd.notnull(row.get('Pump_Model')) else '---',
                'pump_no': int(row.get('Pump_no')) if pd.notnull(row.get('Pump_no')) else 0,
                'motor_model': row.get('Motor_Model')
            }
            session.update(pump_details)
    except Exception as e:
        print(f"Excel Error: {e}")

    if request.method == "POST":
        panel_dist = _to_float(request.form.get("panel_distance") or 0)
        start_method = request.form.get("start_method")

        # --- LOGIC FIX: Simple vs Advanced Head ---
        # If 'total_pipe_head' is missing or 0, we are in Simple Path.
        # We ignore session['final_h'] (which might contain the '100' default) and use 0.
        if session.get("total_pipe_head") and _to_float(session.get("total_pipe_head")) > 0:
            well_head = _to_float(session.get("final_h") or 0)
        else:
            well_head = 0.0

        total_l = panel_dist + well_head
        current = _to_float(session.get('current') or 0)

        # --- FORMULA ---
        # Area = L * I * 0.00278...
        calc_area = total_l * current * 0.0027803571429

        # 'دو ضرب' Multiplier
        if start_method == 'دو ضرب':
            calc_area *= 0.58

        # Vector Match
        vector = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]
        selected_cable = "یافت نشد"
        for size in vector:
            if size >= calc_area:
                selected_cable = size
                break

        session.update({
            'panel_distance': panel_dist,
            'start_method': start_method,
            'selected_cable': selected_cable,
            'calc_area': round(calc_area, 3),
            'total_l_calc': total_l,
            'has_cable': True,  # Flag for result page
            'cable_section': selected_cable  # Consistent naming
        })
        return redirect(url_for("result"))

    # Navigation: Back always goes to Match Page
    return render_template("step_cable.html",
                           pump=pump_details,
                           previous_step=url_for('match_page'))


@app.route('/result')
def result():
    # Detect mode based on whether fittings exist in session
    is_advanced = session.get('fittings_data') is not None

    return render_template(
        'results.html',
        is_pdf=False,
        is_advanced=session.get('fittings_data') is not None,
        pump_model=session.get('selected_model', '---'),
        motor_power=session.get('motor_power', 0),
        pump_efficiency=session.get('pump_efficiency', 0),
        fittings=session.get('fittings_data', []),
        total_pipe_loss=session.get('total_pipe_loss', 0),
        selected_cable=session.get('selected_cable', '---'),
        total_length=session.get('total_l_calc', 0),
        start_method=session.get('start_method', '---'),
        calc_area=session.get('calc_area', 0)
    )


@app.route('/download_report')
def download_report():
    # MUST use the exact same variable names as the result route above
    html = render_template(
        'results.html',
        is_pdf=True,
        is_advanced=session.get('fittings_data') is not None,
        pump_model=session.get('selected_model', '---'),
        motor_power=session.get('motor_power', 0),
        pump_efficiency=session.get('pump_efficiency', 0),
        fittings=session.get('fittings_data', []),
        total_pipe_loss=session.get('total_pipe_loss', 0),
        selected_cable=session.get('selected_cable', '---'),
        total_length=session.get('total_l_calc', 0),
        start_method=session.get('start_method', '---'),
        calc_area=session.get('calc_area', 0)
    )

    # ... your existing options and pdfkit code ...

    # 2. Define the rendering options
    options = {
        'page-size': 'A4',
        'encoding': "UTF-8",
        'margin-top': '10mm',
        'margin-bottom': '10mm',
        'margin-left': '10mm',
        'margin-right': '10mm',
        'no-outline': None,
        'quiet': ''
    }

    try:
        # 3. Use the global PDF_CONFIG and the local variables defined above
        pdf = pdfkit.from_string(
            html,
            False,
            options=options,
            configuration=PDF_CONFIG
        )

        # 4. Prepare the response
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=Technical_Report.pdf'

        return response

    except Exception as e:
        # This will help you debug if wkhtmltopdf fails
        return f"خطا در تولید فایل پی‌دی‌اف: {str(e)}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
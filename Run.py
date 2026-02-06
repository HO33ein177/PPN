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

matplotlib.use('Agg')
from matplotlib import pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, jsonify, session, make_response
import pdfkit

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-me')

@app.route("/guide")
def guide():
    session.clear()
    return render_template("guide.html")
# --- HELPER FUNCTIONS ---

def _normalize_number(s):
    if s is None: return None
    if isinstance(s, (int, float)): return s
    s = str(s).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).replace(',', '.')
    try:
        return float(s)
    except:
        return None


def _to_float(v):
    if v is None: return None
    try:
        return float(str(v).replace(',', '.'))
    except:
        return None


def get_absolute_path(filename):
    root_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(root_dir, 'static', filename)


def parse_list(val):
    if pd.isna(val) or val == '': return None
    try:
        # تبدیل رشته‌های دیتابیس به لیست پایتون
        return ast.literal_eval(str(val))
    except:
        return None


# --- ROUTES ---

@app.route("/")
def welcome():
    session.clear()
    return render_template("welcome.html")


@app.route("/init_simple")
def init_simple():
    session.clear()
    session['mode'] = 'simple'
    return redirect(url_for("simple_input"))


@app.route("/simple_input", methods=["GET", "POST"])
def simple_input():
    error_msg = None
    if request.method == "POST":
        flow = _normalize_number(request.form.get("flow"))
        head = _normalize_number(request.form.get("head"))
        if flow and head:
            if flow > 800:
                error_msg = "دبی بیش از حد مجاز است."
            else:
                session.update({'flow': flow, 'final_q': flow, 'final_h': head})
                return redirect(url_for("match_page"))
    return render_template("simple_input.html", error_msg=error_msg)


@app.route("/init_advanced")
def init_advanced():
    session.clear()
    session['mode'] = 'advanced'
    return redirect(url_for("step1"))


@app.route("/step1", methods=["GET", "POST"])
def step1():
    if request.method == "POST":
        raw_flow = _normalize_number(request.form.get("flow"))
        flow_unit = request.form.get("flow_unit")
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
        total_pipe_head_loss = 0
        flow_m3h = session.get('flow', 0)
        q_m3s = flow_m3h / 3600.0 if flow_m3h else 0

        for idx in sorted(segs.keys(), key=int):
            s = segs[idx]
            L = _to_float(s.get('length') or 0)
            D_mm = _to_float(s.get('diameter') or 0)
            Z = _to_float(s.get('height') or 0)

            # Use 140 for Polyethylene (PE), 100 for Metal (Steel)
            material_name = str(s.get('material', ''))
            C = 140 if "پلی" in material_name else 100

            # Calculate individual row head loss (Friction + Height)
            row_hf = 0
            if L > 0 and D_mm > 0 and q_m3s > 0:
                D_m = D_mm / 1000.0
                row_hf = (10.67 * L * (q_m3s ** 1.852)) / ((C ** 1.852) * (D_m ** 4.871))

            total_row_head = row_hf + Z
            total_pipe_head_loss += total_row_head

            segments.append({
                'length': L,
                'diameter': D_mm,
                'height': Z,
                'material': material_name
            })

        session['segments'] = segments
        session['total_pipe_head'] = total_pipe_head_loss  # Store the sum of all rows
        session['total_other_head'] = _to_float(request.form.get("total_other_head"))
        return redirect(url_for("step_design"))

    # ... (rest of GET logic remains same)

    # --- 2. HANDLE PAGE LOAD (GET) ---
    # Ensure this path matches your actual file location
    file_path = 'data/pipe_Data.xlsx'
    materials_list = []

    try:
        if os.path.exists(file_path):
            # Read the file
            df_mats = pd.read_excel(file_path, sheet_name='Sheet1')

            # CRITICAL: Rename columns to match what step2.html JavaScript expects
            # Based on your file: ['ID', 'Material', 'Diameter', 'Inner_D', 'C_Coef']
            df_mats = df_mats.rename(columns={
                'Material': 'material_group',
                'Diameter': 'size_label',
                'ID': 'index',
                'Inner_D': 'inner_d',
                'C_Coef': 'C'
            })

            # JS also expects 'hydraulic_d'. If missing, use inner_d.
            if 'hydraulic_d' not in df_mats.columns:
                df_mats['hydraulic_d'] = df_mats['inner_d']

            # Clean up: Fill empty values to prevent JavaScript crashes
            df_mats = df_mats.fillna(0)

            # Convert to list of dictionaries
            materials_list = df_mats.to_dict(orient='records')
        else:
            print(f"Warning: File not found at {file_path}")
    except Exception as e:
        print(f"Database Error: {e}")

    # --- 3. RENDER TEMPLATE ---
    # Pass all variables used in the HTML logic
    return render_template(
        "step2.html",
        materials=materials_list,  # Fixes the 'Undefined' JSON error
        flow=session.get('flow', 0),  # Used for systemFlow in JS
        install_depth=session.get('install_depth', 0),  # Used for initial pipe length
        segments=session.get('segments', [])  # Used to restore rows if back button is clicked
    )


@app.route("/step_design", methods=["GET", "POST"])
def step_design():
    # 1. Persistence: Ensure the model name stays in the session
    selected_model_name = request.args.get("model") or session.get("pump_model")
    if selected_model_name:
        session['pump_model'] = selected_model_name

    flow = session.get("flow")
    if not flow:
        return redirect(url_for("welcome"))

    # 2. Fetch Pump Details (Data/cat_Data.xlsx)
    pump_details = {}
    if selected_model_name:
        try:
            df_cat = pd.read_excel('data/cat_Data.xlsx', header=1)
            df_cat['Shoowing_name'] = df_cat['Shoowing_name'].astype(str).str.strip()
            match = df_cat[df_cat['Shoowing_name'] == str(selected_model_name).strip()]

            if not match.empty:
                row = match.iloc[0]
                pump_details = {
                    'showing_name': row.get('Shoowing_name'),
                    'motor_power': row.get('Motor_Power'),
                    'current': _to_float(row.get('current')),
                    'pump_model': int(row.get('Pump_Model')) if pd.notnull(row.get('Pump_Model')) else 'N/A',
                    'pump_no': int(row.get('Pump_no')) if pd.notnull(row.get('Pump_no')) else 0,
                    'motor_model': row.get('Motor_Model')
                }
                session.update(pump_details)
        except Exception as e:
            print(f"Excel Error in step_design: {e}")

    # 3. Calculation: Sum of pipe losses only
    pipe_head = _to_float(session.get("total_pipe_head") or 0)
    other_head = _to_float(session.get("total_other_head") or 0)
    total_h = pipe_head + other_head

    # 4. Handle POST (Form Submission)
    if request.method == "POST":
        session.update({'final_q': flow, 'final_h': total_h})
        return redirect(url_for("match_page"))

    # 5. FIX: Ensure a return statement exists for all GET requests
    return render_template("step_design.html",
                           flow=flow,
                           head=round(total_h, 2),
                           pump=pump_details)


@app.route("/step_cable", methods=["GET", "POST"])
def step_cable():
    # 1. Capture model from URL (Redirected from Match Page)
    selected_model_name = request.args.get("model") or session.get("pump_model")
    if selected_model_name:
        session['pump_model'] = selected_model_name

    pump_details = {}
    # 2. Fetch technical data from Excel
    if selected_model_name:
        try:
            df_cat = pd.read_excel('data/cat_Data.xlsx', header=1)
            df_cat['Shoowing_name'] = df_cat['Shoowing_name'].astype(str).str.strip()
            match = df_cat[df_cat['Shoowing_name'] == str(selected_model_name).strip()]

            if not match.empty:
                row = match.iloc[0]
                pump_details = {
                    'showing_name': row.get('Shoowing_name'),
                    'motor_power': row.get('Motor_Power'),
                    'current': _to_float(row.get('current')),
                    'pump_model': int(row.get('Pump_Model')) if pd.notnull(row.get('Pump_Model')) else 'N/A',
                    'pump_no': int(row.get('Pump_no')) if pd.notnull(row.get('Pump_no')) else 0,
                    'motor_model': row.get('Motor_Model')
                }
                session.update(pump_details)
        except Exception as e:
            print(f"Excel Error: {e}")

    if request.method == "POST":
        # 1. Capture the distance from the form
        panel_dist = _to_float(request.form.get("panel_distance") or 0)
        start_method = request.form.get("start_method")

        # 2. THE FIX: Check if the user came from the 'Advanced' path
        # If 'total_pipe_head' is missing or 0, it means we are in 'Simple' path.
        # We ignore ALL session heads and force it to 0.
        if session.get("total_pipe_head") and _to_float(session.get("total_pipe_head")) > 0:
            well_head = _to_float(session.get("final_h") or 0)
        else:
            well_head = 0.0  # Force zero to kill the "100" ghost value

        # 3. Final Length for calculation
        total_l = panel_dist + well_head
        current = _to_float(session.get('current') or 0)

        # 4. Calculation Formula: L * I * 0.0027803571429
        calc_area = total_l * current * 0.0027803571429

        # Multiplier for 'دو ضرب'
        if start_method == 'دو ضرب':
            calc_area *= 0.58

        # 5. Matching against the vector
        cable_vector = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400]
        selected_cable = "یافت نشد"
        for size in cable_vector:
            if size >= calc_area:
                selected_cable = size
                break

        session.update({
            'panel_distance': panel_dist,
            'start_method': start_method,
            'selected_cable': selected_cable,
            'calc_area': round(calc_area, 3),
            'total_l_calc': total_l,
            'well_head_debug': well_head  # Save this to see it in the result
        })
        return redirect(url_for("result"))

    return render_template("step_cable.html", pump=pump_details)

def get_best_pumps(qr, hr):
    file_path = 'data/cat_Data.xlsx'
    if not os.path.exists(file_path): return [], "فایل دیتابیس یافت نشد."

    try:
        # خواندن دیتابیس از ردیف دوم و نرمال‌سازی نام ستون‌ها
        df = pd.read_excel(file_path, skiprows=1)
        df.columns = [str(c).strip().lower() for c in df.columns]
    except Exception as e:
        return [], f"خطا در خواندن اکسل: {str(e)}"

    results = []
    for _, row in df.iterrows():
        try:
            # استخراج بردارهای اصلی برای رسم نمودار گسسته
            f_vec = parse_list(row.get('flow_vector'))
            h_vec = parse_list(row.get('head_vector'))
            e_vec = parse_list(row.get('efficiency_vector'))

            if not f_vec or not h_vec: continue

            # فیلتر ۱: آیا دبی در محدوده عملکرد پمپ است؟
            if not (min(f_vec) <= qr <= max(f_vec)): continue

            # فیلتر ۲: هد در نقطه دبی (درون‌یابی ساده یا استفاده از چندجمله‌ای)
            h_coeffs = parse_list(row.get('head_flow_poly'))
            if not h_coeffs: continue

            h_func = np.poly1d(h_coeffs)
            calc_h = h_func(qr)

            if calc_h >= hr - 5:  # تلورانس ۵ متری
                e_coeffs = parse_list(row.get('efficiency_flow_poly'))
                e_func = np.poly1d(e_coeffs) if e_coeffs else lambda x: 0

                results.append({
                    'name': str(row.get('shoowing_name', 'Unknown')),
                    'motor_power': _to_float(row.get('motor_power')),
                    'calc_head': round(calc_h, 2),
                    'calc_eff': round(e_func(qr), 1) if e_coeffs else 0,
                    'flow_vec': f_vec,
                    'head_vec': h_vec,
                    'eff_vec': e_vec
                })
        except:
            continue

    if not results: return [], "موردی یافت نشد."
    # رتبه‌بندی: اول توان موتور کمتر، بعد راندمان بیشتر
    return sorted(results, key=lambda x: (x['motor_power'], -x['calc_eff']))[:6], None


# --- MATCHING & API ---

@app.route("/match")
def match_page():
    if not session.get('final_q'): return redirect(url_for("welcome"))
    return render_template("match.html")


@app.route('/api/match')
def api_match():
    qr = session.get('final_q')
    hr = session.get('final_h')

    pumps, error = get_best_pumps(qr, hr)
    if error: return jsonify({"error": error})

    formatted = []
    for p in pumps:
        # ایجاد نقاط برای Chart.js (گسسته)
        h_pts = [{"x": f, "y": h} for f, h in zip(p['flow_vec'], p['head_vec'])]
        e_pts = [{"x": f, "y": e} for f, e in zip(p['flow_vec'], p['eff_vec'])]

        formatted.append({
            "model": p['name'],
            "power": p['motor_power'],
            "predicted_head": p['calc_head'],
            "efficiency": p['calc_eff'],
            "h_points": h_pts,
            "e_points": e_pts,
            "q_min": min(p['flow_vec']) - 5,
            "q_max": max(p['flow_vec']) + 5
        })

    return jsonify({"flow": qr, "required_head": hr, "candidates": formatted})




@app.route("/result")
def result():
    return render_template("results.html",
                           flow=session.get("final_q"),
                           depth=session.get("final_h"),
                           pump_model=session.get("pump_model"),
                           pump_power=session.get("pump_power"),
                           cable_info=session.get("cable_section") if session.get("has_cable") else None)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
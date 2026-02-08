import ast
import base64
import io
import os
import math
import datetime
import platform
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib

# Set backend to Agg to prevent GUI errors on server
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask, request, redirect, url_for, render_template, jsonify, session, make_response
import pdfkit

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-me')


# --- CONFIGURATION & GLOBAL DATA LOADING ---

# 1. Cross-Platform PDF Configuration
def get_pdf_config():
    """
    Detects OS and returns the correct wkhtmltopdf configuration.
    """
    if platform.system() == "Windows":
        # Default dev path, but prefer environment variable if set
        path = os.environ.get('WKHTMLTOPDF_PATH', r'D:\wkhtmltopdf\bin\wkhtmltopdf.exe')
    else:
        # Standard Linux/Production path
        path = os.environ.get('WKHTMLTOPDF_PATH', '/usr/bin/wkhtmltopdf')

    # Check if file exists to prevent hard crashes
    if not os.path.exists(path):
        print(f"WARNING: wkhtmltopdf not found at {path}. PDF generation will fail.")
        return None

    return pdfkit.configuration(wkhtmltopdf=path)


PDF_CONFIG = get_pdf_config()

# 2. Efficient Data Caching (Load Once at Startup)
PIPES_DB = pd.DataFrame()
PUMPS_DB = pd.DataFrame()


def load_data():
    """
    Loads Excel files into global variables once when the app starts.
    """
    global PIPES_DB, PUMPS_DB

    # Load Pipe Data
    pipe_path = 'data/pipe_Data.xlsx'
    if os.path.exists(pipe_path):
        try:
            PIPES_DB = pd.read_excel(pipe_path, sheet_name='Sheet1')
            print(f"SUCCESS: Loaded {len(PIPES_DB)} pipe records.")
        except Exception as e:
            print(f"ERROR: Failed to load pipe data: {e}")
    else:
        print(f"WARNING: Pipe database not found at {pipe_path}")

    # Load Pump/Catalog Data
    cat_path = 'data/cat_Data.xlsx'
    if os.path.exists(cat_path):
        try:
            # Header=1 implies the first row is metadata/empty, and actual headers are on row 2
            df = pd.read_excel(cat_path, header=1)
            # Normalize columns to lowercase for consistent access
            df.columns = [str(c).strip().lower() for c in df.columns]
            PUMPS_DB = df
            print(f"SUCCESS: Loaded {len(PUMPS_DB)} pump records.")
        except Exception as e:
            print(f"ERROR: Failed to load catalog data: {e}")
    else:
        print(f"WARNING: Pump database not found at {cat_path}")


# Execute loading immediately
load_data()


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
        # Check if it's already a list (if pandas inferred it)
        if isinstance(val, list): return val
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
    # Use the globally loaded DataFrame
    global PIPES_DB

    if request.method == "POST":
        segs = defaultdict(dict)
        for k, v in request.form.items():
            parts = k.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                segs[parts[1]][parts[0]] = v

        segments = []
        fittings_data = []
        total_pipe_head_loss = 0
        flow_m3h = session.get('flow', 0)
        q_m3s = flow_m3h / 3600.0 if flow_m3h else 0

        for idx in sorted(segs.keys(), key=int):
            s = segs[idx]
            L = _to_float(s.get('length') or 0)
            D_mm = _to_float(s.get('diameter') or 0)
            Z = _to_float(s.get('height') or 0)

            # THE FIX: mat_id is currently the number '6', not the name 'فلزی'
            mat_id = s.get('material', '')

            display_name = mat_id  # Fallback
            display_size = f"{D_mm} mm"  # Fallback
            C = 100  # Default C

            # Lookup actual labels from the Cached DataFrame
            if not PIPES_DB.empty and mat_id:
                try:
                    # Ensure strict type matching for ID
                    match = PIPES_DB[PIPES_DB['ID'] == int(mat_id)].iloc[0]
                    display_name = match['Material']  # e.g., 'فلزی'
                    display_size = match['Diameter']  # e.g., '4 اینچ'
                    C = match['C_Coef']  # Accurate C from Excel
                except:
                    # If lookup fails, stick to basic logic
                    if "پلی" in str(mat_id): C = 140

            # --- HAZEN-WILLIAMS CALCULATION (PRESERVED) ---
            row_hf = 0
            if L > 0 and D_mm > 0 and q_m3s > 0:
                D_m = D_mm / 1000.0
                row_hf = (10.67 * L * (q_m3s ** 1.852)) / ((C ** 1.852) * (D_m ** 4.871))

            row_total = row_hf + Z
            total_pipe_head_loss += row_total

            segments.append({
                'length': L,
                'diameter': D_mm,
                'height': Z,
                'material': mat_id
            })

            fittings_data.append({
                'name': display_name,
                'size': display_size,
                'quantity': f"{L} m",
                'z_height': Z,
                'loss': round(row_hf, 2)
            })

        session['segments'] = segments
        session['fittings_data'] = fittings_data
        session['total_pipe_head'] = total_pipe_head_loss

        other_loss = _to_float(request.form.get("total_other_head")) or 0
        session['total_other_head'] = other_loss
        if other_loss > 0:
            session['fittings_data'].append({
                'name': 'سایر اتصالات و افت‌ها',
                'size': '-',
                'quantity': '-',
                'z_height': '-',
                'loss': round(other_loss, 2)
            })
            total_pipe_head_loss += other_loss
            session['total_pipe_head'] = total_pipe_head_loss

        return redirect(url_for("step_design"))

    # GET Request Logic (Dropdown population)
    materials_list = []
    if not PIPES_DB.empty:
        try:
            # Prepare data for JS
            df_renamed = PIPES_DB.rename(columns={
                'Material': 'material_group',
                'Diameter': 'size_label',
                'ID': 'index',
                'Inner_D': 'inner_d',
                'C_Coef': 'C'
            })
            df_renamed = df_renamed.fillna(0)
            materials_list = df_renamed.to_dict(orient='records')
        except Exception as e:
            print(f"Data Prep Error: {e}")

    return render_template(
        "step2.html",
        materials=materials_list,
        flow=session.get('flow', 0),
        install_depth=session.get('install_depth', 0),
        segments=session.get('segments', [])
    )


@app.route("/step_design", methods=["GET", "POST"])
def step_design():
    flow = session.get("flow")
    if not flow:
        return redirect(url_for("welcome"))

    pipe_head = _to_float(session.get("total_pipe_head") or 0)
    other_head = _to_float(session.get("total_other_head") or 0)
    total_h = pipe_head + other_head

    selected_model_name = request.args.get("model")
    if selected_model_name:
        session['pump_model'] = selected_model_name

    if request.method == "POST":
        session.update({'final_q': flow, 'final_h': total_h})
        return redirect(url_for("match_page"))

    return render_template("step_design.html",
                           flow=flow,
                           head=round(total_h, 2),
                           previous_step=url_for('step2'))


# --- MATCHING & API LOGIC ---

def get_best_pumps(qr, hr):
    global PUMPS_DB

    if PUMPS_DB.empty:
        return [], "پایگاه داده پمپ‌ها بارگذاری نشده است."

    results = []
    # Iterate over the cached DataFrame
    for _, row in PUMPS_DB.iterrows():
        try:
            # Parse vectors
            f_vec = parse_list(row.get('flow_vector'))
            h_vec = parse_list(row.get('head_vector'))
            e_vec = parse_list(row.get('efficiency_vector'))

            if not f_vec or not h_vec: continue

            # Filter 1: Range check
            if not (min(f_vec) <= qr <= max(f_vec)): continue

            # Filter 2: Calculate Head
            h_coeffs = parse_list(row.get('head_flow_poly'))
            if not h_coeffs:
                calc_h = np.interp(qr, f_vec, h_vec)
            else:
                h_func = np.poly1d(h_coeffs)
                calc_h = h_func(qr)

            # Tolerance check (head must be at least required head - 5m)
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

    if session.get('mode') == 'advanced':
        prev_url = url_for('step_design')
    else:
        prev_url = url_for('simple_input')

    return render_template("match.html", previous_step=prev_url)


@app.route('/api/match')
def api_match():
    qr = _to_float(session.get('final_q'))
    hr = _to_float(session.get('final_h'))

    pumps, error = get_best_pumps(qr, hr)
    if error: return jsonify({"error": error})

    formatted = []
    for p in pumps:
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


# --- CABLE CALCULATION ---

@app.route("/step_cable", methods=["GET", "POST"])
def step_cable():
    global PUMPS_DB

    selected_model = request.args.get("model") or session.get("pump_model")
    if selected_model:
        session['pump_model'] = selected_model

    # Use cached DB for lookup
    pump_details = {}
    if not PUMPS_DB.empty:
        try:
            # Clean column for reliable matching
            # Note: We normalized columns to lower case in load_data()
            # so we access 'shoowing_name' (lowercase)

            # Ensure the lookup value is a string and stripped
            lookup_val = str(selected_model).strip()

            # Filter
            match = PUMPS_DB[PUMPS_DB['shoowing_name'].astype(str).str.strip() == lookup_val]

            if not match.empty:
                row = match.iloc[0]
                pump_details = {
                    'showing_name': row.get('shoowing_name'),
                    'motor_power': row.get('motor_power'),
                    'current': _to_float(row.get('current')),
                    'pump_model': int(row.get('pump_model')) if pd.notnull(row.get('pump_model')) else '---',
                    'pump_no': int(row.get('pump_no')) if pd.notnull(row.get('pump_no')) else 0,
                    'motor_model': row.get('motor_model')
                }
                session.update(pump_details)
        except Exception as e:
            print(f"Data Lookup Error: {e}")

    if request.method == "POST":
        panel_dist = _to_float(request.form.get("panel_distance") or 0)
        start_method = request.form.get("start_method")

        if session.get("total_pipe_head") and _to_float(session.get("total_pipe_head")) > 0:
            well_head = _to_float(session.get("final_h") or 0)
        else:
            well_head = 0.0

        total_l = panel_dist + well_head
        current = _to_float(session.get('current') or 0)

        # Cable Formula Constant
        CABLE_FACTOR = 0.0027803571429
        calc_area = total_l * current * CABLE_FACTOR

        if start_method == 'دو ضرب':
            calc_area *= 0.58

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
            'has_cable': True,
            'cable_section': selected_cable
        })
        return redirect(url_for("result"))

    return render_template("step_cable.html",
                           pump=pump_details,
                           previous_step=url_for('match_page'))


@app.route('/select_pump', methods=['POST'])
def select_pump():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    session['selected_model'] = data.get('model')
    session['motor_power'] = data.get('power')
    session['pump_efficiency'] = data.get('efficiency')

    return jsonify({"status": "success"})


@app.route('/result')
def result():
    fittings_list = session.get('fittings_data', [])
    total_loss = session.get('total_pipe_head', 0)
    pump = session.get('selected_model', '---')

    return render_template(
        'results.html',
        is_pdf=False,
        is_advanced=(len(fittings_list) > 0),
        pump_model=pump,
        motor_power=session.get('motor_power', 0),
        pump_efficiency=session.get('pump_efficiency', 0),
        fittings=fittings_list,
        total_pipe_loss=total_loss,
        selected_cable=session.get('selected_cable', '---'),
        total_length=session.get('total_l_calc', 0),
        start_method=session.get('start_method', '---')
    )


@app.route('/download_report')
def download_report():
    if PDF_CONFIG is None:
        return "خطا: پیکربندی PDF یافت نشد.", 500

    # 1. Helper for Static Files
    def get_absolute_path_pdf(relative_path):
        path = os.path.join(app.root_path, 'static', relative_path)
        return path.replace('\\', '/')

    # 2. Retrieve Data from Session
    static_lvl = _to_float(session.get('static_level'))
    dynamic_lvl = _to_float(session.get('dynamic_level'))
    install_depth = _to_float(session.get('install_depth'))

    # --- FIX 1: CALCULATE SVG COORDINATES FOR WELL SCHEMATIC ---
    # We map the depth (meters) to pixels (0 to 600 height)
    # Ensure there's at least some depth to avoid division by zero
    max_val_in_data = max(install_depth, dynamic_lvl, static_lvl)
    max_depth = max(max_val_in_data, 10) * 1.3

    # Helper to convert meters to pixels relative to SVG top (50px buffer)
    def m_to_px(m):
        if max_depth <= 0: return 50
        return 50 + (m / max_depth * 500)  # 500 is available visual height

    schematic_data = {
        # Values for display text
        'static_val': static_lvl,
        'dynamic_val': dynamic_lvl,
        'install_val': install_depth,

        # Coordinates for drawing lines in SVG
        'static_y': m_to_px(static_lvl),
        'dynamic_y': m_to_px(dynamic_lvl),
        'pump_y': m_to_px(install_depth),
        'water_y': m_to_px(dynamic_lvl),
        'water_height': 600 - m_to_px(dynamic_lvl),

        # Casing widths (visuals)
        'casing_x': 100,
        'casing_width': 100,
        'void_x': 105,
        'void_width': 90
    }

    # --- FIX 2: GENERATE CHART SERVER-SIDE (MATPLOTLIB) ---
    chart_b64 = None
    selected_model = session.get('selected_model')

    # Find the pump data again to plot curves
    pump_row = None
    if not PUMPS_DB.empty and selected_model:
        # Strict string matching
        match = PUMPS_DB[PUMPS_DB['shoowing_name'].astype(str).str.strip() == str(selected_model).strip()]
        if not match.empty:
            pump_row = match.iloc[0]

    if pump_row is not None:
        try:
            # Parse vectors
            f_vec = parse_list(pump_row.get('flow_vector'))
            h_vec = parse_list(pump_row.get('head_vector'))
            e_vec = parse_list(pump_row.get('efficiency_vector'))

            # System Point
            user_q = _to_float(session.get('final_q'))
            user_h = _to_float(session.get('final_h'))

            if f_vec and h_vec:
                # Create Plot
                plt.figure(figsize=(7, 4))

                # Plot H-Q Curve (Left Axis)
                ax1 = plt.gca()
                ax1.plot(f_vec, h_vec, color='#0d6efd', linewidth=2, label='Head (m)')
                ax1.set_xlabel('Flow (m³/h)', fontweight='bold')
                ax1.set_ylabel('Head (m)', color='#0d6efd', fontweight='bold')
                ax1.tick_params(axis='y', labelcolor='#0d6efd')
                ax1.grid(True, alpha=0.3)

                # Plot Operating Point
                ax1.scatter([user_q], [user_h], color='red', s=100, zorder=5, label='Design Point')

                # Plot Efficiency (Right Axis)
                if e_vec:
                    ax2 = ax1.twinx()
                    ax2.plot(f_vec, e_vec, color='#198754', linestyle='--', linewidth=2, label='Eff (%)')
                    ax2.set_ylabel('Efficiency (%)', color='#198754', fontweight='bold')
                    ax2.tick_params(axis='y', labelcolor='#198754')
                    ax2.set_ylim(0, 100)

                # Title
                plt.title(f'Performance Curve: {selected_model}', fontname='DejaVu Sans', fontsize=12)
                plt.tight_layout()

                # Save to Buffer
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=100)
                buf.seek(0)
                chart_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                plt.close()  # Close plot to free memory
        except Exception as e:
            print(f"Chart Generation Error: {e}")

    # 3. Prepare Segments for Table
    raw_segments = session.get('fittings_data', [])
    formatted_segments = []
    for s in raw_segments:
        formatted_segments.append({
            'material_name': s.get('name', '---'),
            'diameter_label': s.get('size', '---'),
            'length': s.get('quantity', '---'),
            'loss': s.get('loss', 0)
        })

    # 4. Context for Template
    report_data = {
        'is_pdf': True,
        'get_absolute_path': get_absolute_path_pdf,
        'date': datetime.datetime.now().strftime('%Y/%m/%d'),

        'flow': session.get('final_q', 0),
        'head': round(session.get('final_h', 0), 2) if session.get('final_h') else 0,

        'static_level': static_lvl,
        'dynamic_level': dynamic_lvl,
        'install_depth': install_depth,
        'diameter_inch': round(session.get('diameter', 0) / 25.4, 1) if session.get('diameter') else '---',

        'schematic': schematic_data,
        'curve_image': chart_b64,  # Pass the generated image

        'pump_model': session.get('selected_model', '---'),
        'pump_power': session.get('motor_power', 0),

        'cable': {
            'length': round(session.get('total_l_calc', 0), 1) if session.get('total_l_calc') else 0,
            'section': session.get('selected_cable', '---'),
            'type': 'flat' if session.get('start_method') == 'دو ضرب' else 'round'
        },
        'segments': formatted_segments
    }

    try:
        html = render_template('report_pdf.html', **report_data)

        options = {
            'page-size': 'A4',
            'encoding': "UTF-8",
            'margin-top': '10mm',
            'margin-bottom': '10mm',
            'margin-left': '10mm',
            'margin-right': '10mm',
            'enable-local-file-access': None,
            'quiet': ''
        }

        pdf = pdfkit.from_string(html, False, options=options, configuration=PDF_CONFIG)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        filename = f"Report_{datetime.datetime.now().strftime('%Y%m%d')}.pdf"
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response

    except Exception as e:
        print(f"PDF Error: {str(e)}")
        return f"PDF Error: {str(e)}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
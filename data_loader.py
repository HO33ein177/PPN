import os
import pandas as pd
import re

_PIPE_DF = None
_PUMP_DF = None

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _read_any(path_without_ext):
    """Try reading xlsx first, then csv. Returns a DataFrame or raises the last exception."""
    xlsx = path_without_ext + ".xlsx"
    csv = path_without_ext + ".csv"
    last_err = None
    if os.path.exists(xlsx):
        try:
            return pd.read_excel(xlsx)
        except Exception as e:
            last_err = e
    if os.path.exists(csv):
        try:
            return pd.read_csv(csv)
        except Exception as e:
            last_err = e
    raise FileNotFoundError(f"Neither {xlsx} nor {csv} could be read: {last_err}")


def load_pipe_data(force=False):
    global _PIPE_DF
    if _PIPE_DF is None or force:
        path = os.path.join(DATA_DIR, "pipe_data")
        _PIPE_DF = _read_any(path)
    return _PIPE_DF


def _parse_diameter_value(val):
    """Returns the raw numeric value found in the string."""
    if pd.isna(val):
        return 0.0
    s_val = str(val).strip()
    match = re.search(r"[-+]?\d*\.\d+|\d+", s_val)
    if not match:
        return 0.0
    return float(match.group())


def get_pipe_materials():
    """
    Return a list of pipe definitions with separated material and size.
    """
    df = load_pipe_data()

    # Identify Columns
    col_name = None
    for c in ("Material", "name", "Name", "نام", "material"):
        if c in df.columns:
            col_name = c
            break

    col_C = None
    for c in ("C_Coef", "C", "c", "C_constant"):
        if c in df.columns:
            col_C = c
            break

    col_diameter = None
    for c in ("Diameter", "diameter", "Size", "size"):
        if c in df.columns:
            col_diameter = c
            break

    col_calc_d = None
    for c in ("Inner_D", "inner_d", "ID_mm", "d_inner"):
        if c in df.columns:
            col_calc_d = c
            break

    col_idx = None
    for c in ("ID", "index", "idx", "ردیف"):
        if c in df.columns:
            col_idx = c
            break

    rows = []
    for i, row in df.iterrows():
        if col_name and pd.isna(row[col_name]):
            continue

        idx = int(row[col_idx]) if col_idx is not None else int(i + 1)
        mat_name = str(row[col_name]).strip() if col_name else str(idx)
        raw_dia_str = str(row[col_diameter]).strip() if col_diameter else ""

        C_val = row[col_C] if col_C is not None else 100

        # Display Value (Raw number from "Diameter" column, e.g. 6)
        display_val = _parse_diameter_value(raw_dia_str)

        # Calculation Value (From "Inner_D" column, e.g. 152.4)
        if col_calc_d and not pd.isna(row[col_calc_d]):
            calc_val = float(row[col_calc_d])
        else:
            calc_val = display_val * 25.4  # Fallback

        rows.append({
            "index": idx,
            "material_group": mat_name,  # For First Dropdown
            "size_label": raw_dia_str,  # For Second Dropdown
            "C": C_val,
            "inner_d": round(display_val, 2),  # Raw number for display
            "hydraulic_d": calc_val  # mm for calc
        })

    return rows


def load_pump_data(force=False):
    global _PUMP_DF
    if _PUMP_DF is None or force:
        path = os.path.join(DATA_DIR, "pump_data")
        _PUMP_DF = _read_any(path)
    return _PUMP_DF


def parse_poly_coeffs(poly_str):
    if pd.isna(poly_str):
        return []
    if isinstance(poly_str, (list, tuple)):
        return [float(x) for x in poly_str]
    if isinstance(poly_str, str):
        parts = [p.strip() for p in poly_str.split(",") if p.strip() != ""]
        try:
            return [float(p) for p in parts]
        except Exception:
            return []
    try:
        return [float(poly_str)]
    except Exception:
        return []
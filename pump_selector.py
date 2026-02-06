"""
Pump selection utilities.
Updated to align with PPTX logic:
1. Primary Filter: Q must be between Q_min and Q_max.
2. Secondary Filter: Pump Head >= Required Head.
3. Sorting: Closest Match (Minimum surplus head).
"""
from typing import List, Optional, Dict
import math
from data_loader import load_pump_data, parse_poly_coeffs

def _eval_poly(coeffs: List[float], q: float) -> Optional[float]:
    if not coeffs:
        return None
    val = 0.0
    for i, c in enumerate(coeffs):
        try:
            val += float(c) * (q ** i)
        except Exception:
            return None
    return val

def select_pumps(desired_q: float,
                 desired_h: float,
                 n: int = 10,
                 show_models: Optional[List[str]] = None,
                 max_power: Optional[float] = None,
                 safety_margin: float = 0.0) -> Dict:
    """Return top-n pump candidates close to (desired_q, desired_h)."""

    df = load_pump_data()
    candidates = []
    show_set = set([s.strip() for s in show_models]) if show_models else None

    for _, r in df.iterrows():
        # 1. Basic Data Extraction
        model = r.get('model') or r.get('مدل پمپ') or r.get('model_name')
        if show_set and str(model) not in show_set:
            continue

        power = None
        try:
            power = float(r.get('توان') or r.get('power') or 0)
        except Exception:
            power = None

        if max_power is not None and power is not None and power > float(max_power):
            continue

        # 2. Primary Filter: Flow Range (PPTX Slide 55)
        # Pump is only valid if Desired Flow is within [Q_min, Q_max]
        qmin = r.get('دبی حداقل') or r.get('Q_min')
        qmax = r.get('دبی حداکثر') or r.get('Q_max')

        is_flow_valid = True
        try:
            if qmin is not None and qmax is not None:
                if not (float(qmin) <= desired_q <= float(qmax)):
                    is_flow_valid = False
        except Exception:
            # If data is corrupt/missing, we assume it's valid to be safe,
            # or you can set to False if you want strict strictness.
            is_flow_valid = True

        if not is_flow_valid:
            continue # Skip this pump immediately

        # 3. Calculate Head (Poly Coeffs)
        poly = parse_poly_coeffs(r.get('poly_coeffs'))
        if not poly:
            continue

        pred = _eval_poly(poly, desired_q)
        if pred is None or (isinstance(pred, float) and (math.isnan(pred) or math.isinf(pred))):
            continue

        # 4. Secondary Filter: Head Requirement (PPTX Slide 56)
        # "Minimum equality of H with f(Q)" -> Predicted Head >= Desired Head
        error = pred - desired_h

        # We store all valid-flow pumps, but we mark if they meet the head requirement
        candidates.append({
            'index': int(r.get('index') or 0),
            'model': model,
            'predicted_head': pred,
            'error': error,
            'abs_error': abs(error),
            'power': power,
            'coeffs': poly,
            'qmin': qmin,
            'qmax': qmax
        })

    # Filter: Keep only pumps that meet the pressure requirement (Head >= Desired)
    # This aligns with "Secondary filter based on min equality of H"
    selectable = [c for c in candidates if c['error'] >= 0]

    # Sort: "Criteria for superiority based on minimum distance" (PPTX Slide 57)
    selectable_sorted = sorted(selectable, key=lambda x: x['abs_error'])

    top = selectable_sorted[:n]
    fallback_used = False

    # Fallback: If no pump meets the head requirement exactly,
    # we might want to show the closest undersized ones (optional),
    # but strictly speaking, the PPTX implies a filter.
    # We will keep the fallback logic just in case the user's requirements are too high for any pump.
    if not top and not selectable:
        # Sort all candidates (who met flow specs) by how close they got to the head
        undershooters = sorted(candidates, key=lambda x: x['abs_error'])
        top = undershooters[:n]
        fallback_used = True

    return {
        'Desired_Q': desired_q,
        'Desired_H': desired_h,
        'total_candidates': len(candidates),
        'fallback': fallback_used,
        'candidates': top
    }
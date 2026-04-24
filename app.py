import math
import xml.etree.ElementTree as ET
from datetime import datetime

import streamlit as st

age_grade_factors = {
    (40, "male"): 0.956,
    (45, "male"): 0.929,
    (50, "male"): 0.898,
    (55, "male"): 0.860,
    (60, "male"): 0.818,
    (65, "male"): 0.790,
    (70, "male"): 0.749,
    (75, "male"): 0.705,
    (80, "male"): 0.660,
    (40, "female"): 0.939,
    (45, "female"): 0.906,
    (50, "female"): 0.867,
    (55, "female"): 0.827,
    (60, "female"): 0.779,
    (65, "female"): 0.735,
    (70, "female"): 0.689,
    (75, "female"): 0.643,
    (80, "female"): 0.596,
}


def linear_interpolate(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def get_closest_factor(age, gender):
    gender = gender.lower()
    keys = sorted([k for k in age_grade_factors if k[1] == gender])
    if not keys:
        return None

    ages = [k[0] for k in keys]
    factors = [age_grade_factors[k] for k in keys]

    if age < ages[0] or age > ages[-1]:
        return None

    for i in range(len(ages) - 1):
        if ages[i] <= age <= ages[i + 1]:
            return linear_interpolate(age, ages[i], ages[i + 1], factors[i], factors[i + 1])

    return factors[-1]


def calculate_temperature_factor(temp_f):
    if temp_f <= 60:
        return 1.0
    elif temp_f <= 70:
        return 0.985
    elif temp_f <= 80:
        return 0.97
    elif temp_f <= 90:
        return 0.92
    else:
        return 0.85


def format_pace(pace, unit):
    minutes = int(pace)
    seconds = int(round((pace - minutes) * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d} min/{unit}"


def format_time_from_minutes(total_minutes):
    total_seconds = int(round(total_minutes * 60))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}h {minutes}m {seconds}s"


def get_namespace(root):
    if root.tag.startswith("{"):
        return {"ns": root.tag.split("}")[0].strip("{")}
    return {"ns": ""}


def parse_iso_time(text):
    if not text:
        return None
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def haversine_meters(lat1, lon1, lat2, lon2):
    radius_m = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def grade_cost_multiplier(grade_pct):
    g = max(-30.0, min(30.0, grade_pct))

    if g >= 0:
        return 1.0 + 0.030 * g + 0.0015 * (g ** 2)

    abs_g = abs(g)
    if abs_g <= 3:
        return max(0.94, 1.0 - 0.018 * abs_g)
    elif abs_g <= 8:
        return max(0.90, 0.946 - 0.006 * (abs_g - 3))
    else:
        return min(1.02, 0.916 + 0.010 * (abs_g - 8))


def calculate_grade_factor_from_totals(distance_value, unit, elevation_gain, elevation_loss, elev_unit):
    if distance_value <= 0:
        return 1.0

    distance_miles = distance_value if unit == "miles" else distance_value * 0.621371

    if elev_unit == "meters":
        gain_ft = elevation_gain * 3.28084
        loss_ft = elevation_loss * 3.28084
    else:
        gain_ft = elevation_gain
        loss_ft = elevation_loss

    gain_per_mile = gain_ft / distance_miles
    loss_per_mile = loss_ft / distance_miles

    uphill_penalty = gain_per_mile * 0.00035
    downhill_credit = loss_per_mile * 0.00010

    factor = 1.0 + uphill_penalty - downhill_credit
    return max(0.92, min(factor, 1.35))


def calculate_grade_factor_from_segments(segments):
    valid = [s for s in segments if s["distance_m"] >= 5]
    if not valid:
        return None

    total_distance = sum(s["distance_m"] for s in valid)
    if total_distance <= 0:
        return None

    weighted_multiplier = sum(
        s["distance_m"] * grade_cost_multiplier(s["grade_pct"])
        for s in valid
    ) / total_distance

    return max(0.92, min(weighted_multiplier, 1.50))


def parse_tcx_file(uploaded_file):
    uploaded_file.seek(0)
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    ns = get_namespace(root)

    if ns["ns"]:
        tracks = root.findall(".//ns:Trackpoint", ns)
        laps = root.findall(".//ns:Lap", ns)
    else:
        tracks = root.findall(".//Trackpoint")
        laps = root.findall(".//Lap")

    total_distance_m = 0.0
    total_time_s = 0.0

    for lap in laps:
        time_el = lap.find("ns:TotalTimeSeconds", ns) if ns["ns"] else lap.find("TotalTimeSeconds")
        dist_el = lap.find("ns:DistanceMeters", ns) if ns["ns"] else lap.find("DistanceMeters")
        if time_el is not None and time_el.text:
            total_time_s += float(time_el.text)
        if dist_el is not None and dist_el.text:
            total_distance_m += float(dist_el.text)

    elevations = []
    for tp in tracks:
        alt_el = tp.find("ns:AltitudeMeters", ns) if ns["ns"] else tp.find("AltitudeMeters")
        if alt_el is not None and alt_el.text:
            try:
                elevations.append(float(alt_el.text))
            except ValueError:
                pass

    elevation_gain_m = 0.0
    elevation_loss_m = 0.0
    for i in range(1, len(elevations)):
        delta = elevations[i] - elevations[i - 1]
        if delta > 0:
            elevation_gain_m += delta
        elif delta < 0:
            elevation_loss_m += abs(delta)

    return {
        "source_format": "TCX",
        "distance_km": total_distance_m / 1000 if total_distance_m else None,
        "distance_miles": total_distance_m / 1609.34 if total_distance_m else None,
        "elapsed_minutes": total_time_s / 60 if total_time_s else None,
        "elevation_gain_m": elevation_gain_m if elevations else None,
        "elevation_loss_m": elevation_loss_m if elevations else None,
        "elevation_gain_ft": elevation_gain_m * 3.28084 if elevations else None,
        "elevation_loss_ft": elevation_loss_m * 3.28084 if elevations else None,
        "segments": None,
    }


def parse_gpx_file(uploaded_file):
    uploaded_file.seek(0)
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    ns = get_namespace(root)

    if ns["ns"]:
        trackpoints = root.findall(".//ns:trkpt", ns)
    else:
        trackpoints = root.findall(".//trkpt")

    if not trackpoints:
        raise ValueError("No track points found in GPX file.")

    total_distance_m = 0.0
    elevation_gain_m = 0.0
    elevation_loss_m = 0.0
    times = []
    segments = []

    prev_lat = prev_lon = prev_ele = None

    for tp in trackpoints:
        lat = tp.attrib.get("lat")
        lon = tp.attrib.get("lon")
        if lat is None or lon is None:
            continue

        lat = float(lat)
        lon = float(lon)

        ele_el = tp.find("ns:ele", ns) if ns["ns"] else tp.find("ele")
        time_el = tp.find("ns:time", ns) if ns["ns"] else tp.find("time")

        ele = None
        if ele_el is not None and ele_el.text:
            try:
                ele = float(ele_el.text)
            except ValueError:
                ele = None

        dt = parse_iso_time(time_el.text) if time_el is not None and time_el.text else None
        if dt is not None:
            times.append(dt)

        if prev_lat is not None and prev_lon is not None:
            dist_m = haversine_meters(prev_lat, prev_lon, lat, lon)
            total_distance_m += dist_m

            if prev_ele is not None and ele is not None:
                delta = ele - prev_ele
                if delta > 0:
                    elevation_gain_m += delta
                elif delta < 0:
                    elevation_loss_m += abs(delta)

                if dist_m >= 5:
                    grade_pct = (delta / dist_m) * 100
                    grade_pct = max(-30.0, min(30.0, grade_pct))
                    segments.append({
                        "distance_m": dist_m,
                        "grade_pct": grade_pct,
                    })

        prev_lat, prev_lon, prev_ele = lat, lon, ele

    elapsed_minutes = None
    if len(times) >= 2:
        elapsed_minutes = (times[-1] - times[0]).total_seconds() / 60

    return {
        "source_format": "GPX",
        "distance_km": total_distance_m / 1000 if total_distance_m else None,
        "distance_miles": total_distance_m / 1609.34 if total_distance_m else None,
        "elapsed_minutes": elapsed_minutes,
        "elevation_gain_m": elevation_gain_m if elevation_gain_m else 0.0,
        "elevation_loss_m": elevation_loss_m if elevation_loss_m else 0.0,
        "elevation_gain_ft": elevation_gain_m * 3.28084 if elevation_gain_m is not None else None,
        "elevation_loss_ft": elevation_loss_m * 3.28084 if elevation_loss_m is not None else None,
        "segments": segments,
    }


def parse_uploaded_activity(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".tcx"):
        return parse_tcx_file(uploaded_file)
    if name.endswith(".gpx"):
        return parse_gpx_file(uploaded_file)
    raise ValueError("Unsupported file type. Please upload a .tcx or .gpx file.")


st.set_page_config(page_title="Running Pace Adjuster", page_icon="🏃")

st.title("🏃 Running Pace Adjuster")

st.write(
    """
This tool estimates how your running result might look after adjusting for age,
temperature, and hilliness. You can enter race information manually, or upload a
**TCX or GPX file** to auto-fill distance, moving time, elevation gain, and elevation loss.
For GPX files, the hill adjustment uses a **segment-based** model.
"""
)

st.subheader("Optional activity upload")
uploaded_activity = st.file_uploader("Upload a TCX or GPX file", type=["tcx", "gpx"])

activity_data = None
if uploaded_activity is not None:
    try:
        activity_data = parse_uploaded_activity(uploaded_activity)
        st.success(f"{activity_data['source_format']} file loaded and values applied where available.")
    except Exception as exc:
        st.error(f"Could not read uploaded file: {exc}")

st.subheader("Adjustment options")
col_toggle_1, col_toggle_2, col_toggle_3 = st.columns(3)
with col_toggle_1:
    use_age = st.toggle(
        "Age",
        value=True,
        help="Adjusts performance using interpolated age-grade factors to estimate how the result compares with peak-age performance.",
    )
with col_toggle_2:
    use_temp = st.toggle(
        "Temperature",
        value=False,
        help="Adjusts time for heat using a simple temperature factor. Warmer conditions increase the adjustment more than mild conditions.",
    )
with col_toggle_3:
    use_grade = st.toggle(
        "Hills",
        value=False,
        help="For GPX uploads, uses segment-by-segment grade. For manual entry or TCX uploads, uses an estimate based on total elevation gain and loss.",
    )

with st.form("pace_adjuster_form"):
    st.markdown("### Runner Profile")
    runner_col1, runner_col2 = st.columns(2)
    with runner_col1:
        age = st.number_input("Age", min_value=1, max_value=120, value=50, step=1)
    with runner_col2:
        gender = st.selectbox("Gender", ["male", "female"])

    st.markdown("### Run Details")
    detail_col1, detail_col2 = st.columns(2)

    with detail_col1:
        unit = st.selectbox("Distance unit", ["miles", "km"])

        default_distance = 5.0
        if activity_data:
            pulled_distance = activity_data["distance_miles"] if unit == "miles" else activity_data["distance_km"]
            if pulled_distance is not None:
                default_distance = pulled_distance

        distance_input = st.number_input(
            f"Race distance ({unit})",
            min_value=0.1,
            value=float(default_distance),
            step=0.1,
        )

        if use_temp:
            temperature_f = st.number_input("Race temperature (°F)", value=70.0, step=1.0)
        else:
            temperature_f = 60.0

    with detail_col2:
        if use_grade:
            elevation_unit = st.selectbox("Elevation unit", ["feet", "meters"])

            default_gain = 0.0
            default_loss = 0.0
            if activity_data:
                if elevation_unit == "feet":
                    default_gain = activity_data["elevation_gain_ft"] or 0.0
                    default_loss = activity_data["elevation_loss_ft"] or 0.0
                else:
                    default_gain = activity_data["elevation_gain_m"] or 0.0
                    default_loss = activity_data["elevation_loss_m"] or 0.0

            elevation_gain = st.number_input(
                f"Elevation gain ({elevation_unit})",
                min_value=0.0,
                value=float(default_gain),
                step=10.0,
            )
            elevation_loss = st.number_input(
                f"Elevation loss ({elevation_unit})",
                min_value=0.0,
                value=float(default_loss),
                step=10.0,
            )
        else:
            elevation_unit = "feet"
            elevation_gain = 0.0
            elevation_loss = 0.0

    st.markdown("### Moving Time")
    time_col1, time_col2, time_col3 = st.columns(3)

    default_hours = 0
    default_minutes = 25
    default_seconds = 0
    if activity_data and activity_data["elapsed_minutes"] is not None:
        total_seconds = int(round(activity_data["elapsed_minutes"] * 60))
        default_hours = total_seconds // 3600
        default_minutes = (total_seconds % 3600) // 60
        default_seconds = total_seconds % 60

    with time_col1:
        hours = st.number_input("Hours", min_value=0, value=int(default_hours), step=1)
    with time_col2:
        minutes = st.number_input("Minutes", min_value=0, max_value=59, value=int(default_minutes), step=1)
    with time_col3:
        seconds = st.number_input("Seconds", min_value=0, max_value=59, value=int(default_seconds), step=1)

    submitted = st.form_submit_button("Calculate")

if submitted:
    raw_time_minutes = hours * 60 + minutes + seconds / 60

    if distance_input <= 0:
        st.error("Distance must be greater than zero.")
    elif raw_time_minutes <= 0:
        st.error("Moving time must be greater than zero.")
    else:
        raw_pace = raw_time_minutes / distance_input

        st.subheader("Results")
        st.write(f"**Original pace:** {format_pace(raw_pace, unit)}")
        st.write(f"**Original moving time:** {format_time_from_minutes(raw_time_minutes)}")

        adjusted_time = raw_time_minutes
        applied_adjustments = []
        hill_method = None
        hill_factor = None

        if use_temp:
            temp_factor = calculate_temperature_factor(temperature_f)
            adjusted_time *= temp_factor
            applied_adjustments.append("temperature")

        if use_grade:
            if activity_data and activity_data.get("source_format") == "GPX" and activity_data.get("segments"):
                hill_factor = calculate_grade_factor_from_segments(activity_data["segments"])
                hill_method = "segment-based GPX model"
            else:
                hill_factor = calculate_grade_factor_from_totals(
                    distance_value=distance_input,
                    unit=unit,
                    elevation_gain=elevation_gain,
                    elevation_loss=elevation_loss,
                    elev_unit=elevation_unit,
                )
                hill_method = "total gain/loss estimate"

            if hill_factor is not None:
                adjusted_time /= hill_factor
                applied_adjustments.append("hilliness")

        if use_age:
            age_factor = get_closest_factor(age, gender)
            if age_factor is not None:
                adjusted_time *= age_factor
                applied_adjustments.append("age")
            else:
                st.warning("No age-adjustment factor found for that age. Age adjustment was skipped.")

        adjusted_pace = adjusted_time / distance_input

        st.write(f"**Adjusted time:** {format_time_from_minutes(adjusted_time)}")
        st.write(f"**Adjusted pace:** {format_pace(adjusted_pace, unit)}")

        if applied_adjustments:
            st.write("**Adjustments applied:** " + ", ".join(applied_adjustments).capitalize())
        else:
            st.write("**Adjustments applied:** none")

        if use_grade and hill_factor is not None:
            st.write(f"**Hill factor used:** {hill_factor:.3f}")
            st.write(f"**Hill method used:** {hill_method}")
            st.caption(
                "Hill adjustment is still an estimate. GPX uploads use segment-by-segment grade; "
                "manual entry and TCX use a total gain/loss fallback."
            )

        improvement = raw_time_minutes - adjusted_time
        if improvement > 0:
            percent = improvement / raw_time_minutes * 100
            st.success(f"Your adjusted time is {percent:.1f}% faster than your actual time.")
        elif improvement < 0:
            percent = abs(improvement) / raw_time_minutes * 100
            st.info(f"Your adjusted time is {percent:.1f}% slower than your actual time.")
        else:
            st.info("No change from the selected adjustments.")

st.caption("Carbon footprint of running this app: ~0.01 miles driven by a gasoline-powered car.")

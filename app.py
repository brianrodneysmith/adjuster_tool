import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import streamlit as st


MALE_TABLE_FILE = "MaleRoadStd2025.xlsx"
FEMALE_TABLE_FILE = "FemaleRoadStd2025.xlsx"

DISTANCE_PRESETS_KM = {
    "Custom": None,
    "5K": 5.0,
    "10K": 10.0,
    "Half marathon": 21.0975,
    "Marathon": 42.195,
}


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


def seconds_to_minutes(seconds):
    return seconds / 60


def km_to_display_distance(distance_km, unit):
    if unit == "miles":
        return distance_km / 1.60934
    return distance_km


def parse_optional_float(text):
    """Parse an optional numeric text input. Blank values return None."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def calculate_dew_point_f(temp_f, humidity_pct):
    """Estimate dew point in °F from temperature °F and relative humidity %."""
    if temp_f is None or humidity_pct is None or humidity_pct <= 0:
        return None

    temp_c = (temp_f - 32) * 5 / 9
    rh = max(1e-6, min(100, humidity_pct)) / 100
    a = 17.625
    b = 243.04
    gamma = math.log(rh) + (a * temp_c) / (b + temp_c)
    dew_point_c = (b * gamma) / (a - gamma)
    return dew_point_c * 9 / 5 + 32


def calculate_dew_point_factor(dew_point_f):
    """
    Conservative dew-point factor for equivalent moderate-condition pace.
    Lower factors make the adjusted equivalent pace faster.
    """
    if dew_point_f is None:
        return 1.0
    if dew_point_f <= 55:
        return 1.0
    if dew_point_f <= 60:
        return 0.995
    if dew_point_f <= 65:
        return 0.985
    if dew_point_f <= 70:
        return 0.970
    if dew_point_f <= 75:
        return 0.940
    return 0.900


def calculate_humidity_only_factor(humidity_pct):
    """
    Very conservative fallback when humidity is entered without temperature.
    Humidity alone is not enough to estimate heat stress precisely.
    """
    if humidity_pct is None:
        return 1.0
    if humidity_pct <= 70:
        return 1.0
    if humidity_pct <= 85:
        return 0.995
    return 0.990


def calculate_dew_point_extra_slowdown(dew_point_f):
    """
    Modest added weather penalty from high dew point.
    This is added to the temperature baseline, then capped in calculate_weather_factor.
    """
    if dew_point_f is None:
        return 0.0
    if dew_point_f <= 60:
        return 0.0
    if dew_point_f <= 65:
        return 0.005
    if dew_point_f <= 70:
        return 0.015
    if dew_point_f <= 75:
        return 0.030
    return 0.050


def calculate_weather_factor(temp_f, humidity_pct):
    """
    Estimate a single weather factor from temperature and optional humidity.
    Temperature creates the baseline adjustment. When humidity is provided,
    high dew point can add a modest extra penalty, with a cap to avoid over-adjusting.
    """
    if temp_f is None and humidity_pct is None:
        return None

    details = []
    dew_point_f = None
    dew_point_extra = 0.0

    if temp_f is not None:
        temp_factor = calculate_temperature_factor(temp_f)
        baseline_slowdown = 1.0 - temp_factor
        details.append(f"temperature {temp_f:.0f}°F")

        if humidity_pct is not None:
            dew_point_f = calculate_dew_point_f(temp_f, humidity_pct)
            dew_point_extra = calculate_dew_point_extra_slowdown(dew_point_f)
            details.append(f"humidity {humidity_pct:.0f}%")
            if dew_point_f is not None:
                details.append(f"estimated dew point {dew_point_f:.0f}°F")

        total_slowdown = baseline_slowdown + dew_point_extra
        total_slowdown = min(total_slowdown, 0.18)
        factor = max(0.82, 1.0 - total_slowdown)
    else:
        # Humidity alone is not enough to estimate full heat stress, so use a
        # conservative fallback rather than inferring a dew point.
        factor = calculate_humidity_only_factor(humidity_pct)
        details.append(f"humidity {humidity_pct:.0f}%")

    return {
        "factor": factor,
        "details": ", ".join(details),
        "dew_point_f": dew_point_f,
        "dew_point_extra": dew_point_extra,
    }


def render_how_estimate_works(adjustment_key):
    if adjustment_key == "age":
        with st.expander("How the age estimate works"):
            st.write("This app uses standard road age-grading tables to estimate how your performance compares across ages. The tables provide benchmark times for each age, gender, and distance. Age grade compares your time with standard top performances for your age, gender, and distance. Your time is also adjusted to an equivalent peak-age performance when your age is covered by the tables. Age grading is a comparison tool rather than a prediction of what you personally would have run at a different age.")
    elif adjustment_key == "weather":
        with st.expander("How the weather estimate works"):
            st.write("Weather affects running mostly through heat stress and the body’s ability to cool itself. This app uses temperature and, when provided, humidity to estimate your equivalent pace in moderate weather. Temperature creates the baseline weather adjustment; when humidity is provided, a high dew point can add a modest extra penalty. The total adjustment is capped to avoid over-adjusting. Other tools, including VDOT, McMillan, and Running Writings, use different weather models, so their estimates may differ.")
    elif adjustment_key == "hills":
        with st.expander("How the hill estimate works"):
            st.write("Hills affect pace because uphill running requires extra effort, while downhill running usually gives only a partial benefit. For uploaded GPX files, this app estimates grade from the route’s elevation profile. For manual entries or TCX files, it uses total elevation gain and loss as a simpler fallback. This is similar in purpose to metrics like Strava GAP, but it is not Strava’s model. Different platforms smooth elevation data and estimate grade-adjusted effort differently, so results may differ.")
    elif adjustment_key == "altitude":
        with st.expander("How the altitude estimate works"):
            st.write("Altitude affects running because there is less oxygen available at higher elevations. This app estimates what your pace would have been at sea level, where oxygen availability is greater. The adjustment is most relevant for runs at moderate or high elevation. It does not account for personal acclimatization, which can make a large difference in how much altitude affects performance.")


def calculate_altitude_factor(altitude_ft):
    """
    Conservative non-linear estimate of equivalent sea-level performance.
    No adjustment is applied below roughly 1,000 ft.
    """
    if altitude_ft <= 1000:
        return 1.0

    thousands_above_baseline = (altitude_ft - 1000) / 1000
    slowdown = 0.005 * thousands_above_baseline + 0.00125 * (thousands_above_baseline ** 2)
    slowdown = max(0.0, min(slowdown, 0.15))
    return 1.0 - slowdown


def format_coordinates(lat, lon):
    if lat is None or lon is None:
        return None
    return f"{lat:.5f}, {lon:.5f}"


def get_age_grade_label(pct_as_decimal):
    if pct_as_decimal >= 0.90:
        return "world class"
    elif pct_as_decimal >= 0.80:
        return "national class"
    elif pct_as_decimal >= 0.70:
        return "regional class"
    elif pct_as_decimal >= 0.60:
        return "competitive recreational"
    elif pct_as_decimal >= 0.50:
        return "recreational"
    else:
        return "developing"


def col_letters_to_index(letters):
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


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


def read_xlsx_sheet(path, sheet_xml):
    with ZipFile(path) as z:
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("a:si", ns):
                shared_strings.append("".join(t.text or "" for t in si.findall(".//a:t", ns)))

        root = ET.fromstring(z.read(sheet_xml))
        ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = []

        for row in root.findall("a:sheetData/a:row", ns):
            row_values = {}
            for cell in row.findall("a:c", ns):
                ref = cell.attrib.get("r", "")
                match = re.match(r"([A-Z]+)(\d+)", ref)
                if not match:
                    continue

                col_index = col_letters_to_index(match.group(1))
                value_node = cell.find("a:v", ns)

                if value_node is None:
                    value = None
                else:
                    value = value_node.text
                    if cell.attrib.get("t") == "s":
                        value = shared_strings[int(value)]
                    else:
                        try:
                            value = float(value)
                            if value.is_integer():
                                value = int(value)
                        except ValueError:
                            pass

                row_values[col_index] = value

            rows.append(row_values)

        return rows


@st.cache_data
def load_age_standard_table(gender):
    gender_key = gender.lower()
    filename = MALE_TABLE_FILE if gender_key == "male" else FEMALE_TABLE_FILE
    path = Path(__file__).resolve().parent / filename

    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {filename}. Place it in the same folder as app.py."
        )

    rows = read_xlsx_sheet(path, "xl/worksheets/sheet2.xml")

    header_row = rows[1]
    distance_row = rows[2]
    open_standard_row = rows[3]

    distances = []
    open_standards = {}

    for col, label in header_row.items():
        if col == 1 or label is None:
            continue
        distance_km = distance_row.get(col)
        open_seconds = open_standard_row.get(col)
        if isinstance(distance_km, (int, float)) and isinstance(open_seconds, (int, float)):
            distances.append((float(distance_km), col, str(label)))
            open_standards[float(distance_km)] = float(open_seconds)

    age_standards = {}
    for row in rows[5:]:
        age = row.get(1)
        if not isinstance(age, (int, float)):
            continue
        age = int(age)
        values = {}
        for distance_km, col, label in distances:
            standard = row.get(col)
            if isinstance(standard, (int, float)):
                values[distance_km] = float(standard)
        if values:
            age_standards[age] = values

    distances_km = sorted(open_standards.keys())

    return {
        "distances_km": distances_km,
        "open_standards": open_standards,
        "age_standards": age_standards,
        "source_file": filename,
    }


def interpolate_by_log_distance(distance_km, value_by_distance):
    distances = sorted(value_by_distance.keys())

    if distance_km <= distances[0]:
        return value_by_distance[distances[0]], distances[0], distances[0]
    if distance_km >= distances[-1]:
        return value_by_distance[distances[-1]], distances[-1], distances[-1]

    lower = distances[0]
    upper = distances[-1]

    for i in range(len(distances) - 1):
        if distances[i] <= distance_km <= distances[i + 1]:
            lower = distances[i]
            upper = distances[i + 1]
            break

    if lower == upper:
        return value_by_distance[lower], lower, upper

    x = (math.log(distance_km) - math.log(lower)) / (math.log(upper) - math.log(lower))
    interpolated = value_by_distance[lower] + x * (value_by_distance[upper] - value_by_distance[lower])
    return interpolated, lower, upper


def calculate_age_grading(actual_minutes, age, gender, distance_value, unit):
    distance_km = distance_value * 1.60934 if unit == "miles" else distance_value

    table = load_age_standard_table(gender)
    age_standards = table["age_standards"]

    if age not in age_standards:
        min_age = min(age_standards.keys())
        max_age = max(age_standards.keys())
        return {
            "applied": False,
            "reason": f"Age adjustment is available for ages covered by the age-grading tables. This table covers ages {min_age} through {max_age}.",
        }

    age_standard_seconds, lower_d, upper_d = interpolate_by_log_distance(
        distance_km, age_standards[age]
    )
    open_standard_seconds, _, _ = interpolate_by_log_distance(
        distance_km, table["open_standards"]
    )

    actual_seconds = actual_minutes * 60
    age_factor = open_standard_seconds / age_standard_seconds
    age_adjusted_seconds = actual_seconds * age_factor
    age_grade_pct = age_standard_seconds / actual_seconds

    return {
        "applied": True,
        "distance_km": distance_km,
        "source_file": table["source_file"],
        "age_standard_seconds": age_standard_seconds,
        "open_standard_seconds": open_standard_seconds,
        "age_factor": age_factor,
        "age_adjusted_minutes": seconds_to_minutes(age_adjusted_seconds),
        "age_adjusted_pace_close_to_actual": abs(age_adjusted_seconds - actual_seconds) / actual_seconds <= 0.005,
        "age_grade_pct": age_grade_pct,
        "age_grade_label": get_age_grade_label(age_grade_pct),
        "interpolated": lower_d != upper_d,
        "lower_distance_km": lower_d,
        "upper_distance_km": upper_d,
    }


def build_rolling_grade_segments(points, target_segment_m=50):
    """
    Build grade segments over accumulated distance rather than point-to-point.
    This prevents dense GPX files from having nearly all points ignored.
    Each point is a tuple: (lat, lon, elevation_m).
    """
    segments = []
    if len(points) < 2:
        return segments

    start_lat, start_lon, start_ele = points[0]
    prev_lat, prev_lon, prev_ele = points[0]
    accumulated_distance = 0.0

    for lat, lon, ele in points[1:]:
        step_distance = haversine_meters(prev_lat, prev_lon, lat, lon)
        accumulated_distance += step_distance

        if accumulated_distance >= target_segment_m:
            if start_ele is not None and ele is not None and accumulated_distance > 0:
                delta_ele = ele - start_ele
                grade_pct = (delta_ele / accumulated_distance) * 100
                grade_pct = max(-30.0, min(30.0, grade_pct))
                segments.append({
                    "distance_m": accumulated_distance,
                    "grade_pct": grade_pct,
                })

            start_lat, start_lon, start_ele = lat, lon, ele
            accumulated_distance = 0.0

        prev_lat, prev_lon, prev_ele = lat, lon, ele

    return segments


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
    coordinates = []
    for tp in tracks:
        alt_el = tp.find("ns:AltitudeMeters", ns) if ns["ns"] else tp.find("AltitudeMeters")
        if alt_el is not None and alt_el.text:
            try:
                elevations.append(float(alt_el.text))
            except ValueError:
                pass

        if ns["ns"]:
            lat_el = tp.find("ns:Position/ns:LatitudeDegrees", ns)
            lon_el = tp.find("ns:Position/ns:LongitudeDegrees", ns)
        else:
            lat_el = tp.find("Position/LatitudeDegrees")
            lon_el = tp.find("Position/LongitudeDegrees")
        if lat_el is not None and lon_el is not None and lat_el.text and lon_el.text:
            try:
                coordinates.append((float(lat_el.text), float(lon_el.text)))
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
        "start_lat": coordinates[0][0] if coordinates else None,
        "start_lon": coordinates[0][1] if coordinates else None,
        "end_lat": coordinates[-1][0] if coordinates else None,
        "end_lon": coordinates[-1][1] if coordinates else None,
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
    points = []

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

        points.append((lat, lon, ele))

        if prev_lat is not None and prev_lon is not None:
            dist_m = haversine_meters(prev_lat, prev_lon, lat, lon)
            total_distance_m += dist_m

            if prev_ele is not None and ele is not None:
                delta = ele - prev_ele
                if delta > 0:
                    elevation_gain_m += delta
                elif delta < 0:
                    elevation_loss_m += abs(delta)

        prev_lat, prev_lon, prev_ele = lat, lon, ele

    elapsed_minutes = None
    if len(times) >= 2:
        elapsed_minutes = (times[-1] - times[0]).total_seconds() / 60

    segments = build_rolling_grade_segments(points, target_segment_m=50)

    return {
        "source_format": "GPX",
        "distance_km": total_distance_m / 1000 if total_distance_m else None,
        "distance_miles": total_distance_m / 1609.34 if total_distance_m else None,
        "elapsed_minutes": elapsed_minutes,
        "elevation_gain_m": elevation_gain_m if elevation_gain_m else 0.0,
        "elevation_loss_m": elevation_loss_m if elevation_loss_m else 0.0,
        "elevation_gain_ft": elevation_gain_m * 3.28084 if elevation_gain_m is not None else None,
        "elevation_loss_ft": elevation_loss_m * 3.28084 if elevation_loss_m is not None else None,
        "start_lat": points[0][0] if points else None,
        "start_lon": points[0][1] if points else None,
        "end_lat": points[-1][0] if points else None,
        "end_lon": points[-1][1] if points else None,
        "segments": segments,
        "segment_model": "rolling 50-meter segments",
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
This tool estimates how your running result might look after adjusting for selected factors:
**age**, **weather**, **hills**, and **altitude**. You can enter run information manually,
or upload a **TCX or GPX file** to auto-fill distance, time, elevation gain, and elevation loss.
"""
)

st.subheader("Optional activity upload")
uploaded_activity = st.file_uploader("Upload a TCX or GPX file", type=["tcx", "gpx"])

activity_data = None
if uploaded_activity is not None:
    try:
        activity_data = parse_uploaded_activity(uploaded_activity)
        st.success(f"{activity_data['source_format']} file loaded and values applied where available.")
        start_coordinates = format_coordinates(activity_data.get("start_lat"), activity_data.get("start_lon"))
        end_coordinates = format_coordinates(activity_data.get("end_lat"), activity_data.get("end_lon"))
        if start_coordinates:
            if end_coordinates and end_coordinates != start_coordinates:
                st.caption(f"Route coordinates: start {start_coordinates}; end {end_coordinates}.")
            else:
                st.caption(f"Route coordinates: {start_coordinates}.")
    except Exception as exc:
        st.error(f"Could not read uploaded file: {exc}")

st.subheader("Adjustment options")
col_toggle_1, col_toggle_2, col_toggle_3, col_toggle_4 = st.columns(4)
with col_toggle_1:
    use_age = st.toggle(
        "Age",
        value=True,
        help="Adjusts your time for age using standard age-grading tables, then compares the adjusted result to the peak-age standard for the same distance.",
    )
with col_toggle_2:
    use_weather = st.toggle(
        "Weather",
        value=False,
        help="Adjusts your pace for the combined effects of temperature and humidity using running weather conditions.",
    )
with col_toggle_3:
    use_grade = st.toggle(
        "Hills",
        value=False,
        help="Adjusts your pace for hills by estimating the added effort of uphill running and the limited benefit of downhills.",
    )
with col_toggle_4:
    use_altitude = st.toggle(
        "Altitude",
        value=False,
        help="Adjusts your pace for reduced oxygen availability at elevation and estimates an equivalent sea-level pace.",
    )

preset_options = ["Custom", "5K", "10K", "Half marathon", "Marathon"]


def uploaded_distance_for_unit(unit_value):
    if not activity_data:
        return None
    return activity_data["distance_miles"] if unit_value == "miles" else activity_data["distance_km"]


def default_distance_for_current_settings():
    unit_value = st.session_state.get("distance_unit_selector", "miles")
    preset_value = st.session_state.get("distance_preset_selector", "Custom")

    if DISTANCE_PRESETS_KM[preset_value] is not None:
        return float(km_to_display_distance(DISTANCE_PRESETS_KM[preset_value], unit_value))

    uploaded_distance = uploaded_distance_for_unit(unit_value)
    if uploaded_distance is not None:
        return float(uploaded_distance)

    return 5.0


def sync_distance_from_preset():
    unit_value = st.session_state.get("distance_unit_selector", "miles")
    preset_value = st.session_state.get("distance_preset_selector", "Custom")

    if DISTANCE_PRESETS_KM[preset_value] is not None:
        st.session_state["distance_input_value"] = float(
            km_to_display_distance(DISTANCE_PRESETS_KM[preset_value], unit_value)
        )


def sync_distance_on_unit_change():
    new_unit = st.session_state.get("distance_unit_selector", "miles")
    old_unit = st.session_state.get("previous_distance_unit", new_unit)
    preset_value = st.session_state.get("distance_preset_selector", "Custom")

    if DISTANCE_PRESETS_KM[preset_value] is not None:
        sync_distance_from_preset()
    elif old_unit != new_unit and "distance_input_value" in st.session_state:
        current_distance = st.session_state["distance_input_value"]
        if old_unit == "miles" and new_unit == "km":
            st.session_state["distance_input_value"] = float(current_distance) * 1.60934
        elif old_unit == "km" and new_unit == "miles":
            st.session_state["distance_input_value"] = float(current_distance) / 1.60934

    st.session_state["previous_distance_unit"] = new_unit


if "distance_unit_selector" not in st.session_state:
    st.session_state["distance_unit_selector"] = "miles"
if "previous_distance_unit" not in st.session_state:
    st.session_state["previous_distance_unit"] = st.session_state["distance_unit_selector"]
if "distance_preset_selector" not in st.session_state:
    st.session_state["distance_preset_selector"] = "Custom"
if "distance_input_value" not in st.session_state:
    st.session_state["distance_input_value"] = default_distance_for_current_settings()

activity_distance_signature = None
if uploaded_activity is not None:
    activity_distance_signature = f"{uploaded_activity.name}:{uploaded_activity.size}"
if activity_distance_signature != st.session_state.get("activity_distance_signature"):
    st.session_state["activity_distance_signature"] = activity_distance_signature
    if (
        activity_distance_signature is not None
        and st.session_state.get("distance_preset_selector", "Custom") == "Custom"
    ):
        uploaded_distance = uploaded_distance_for_unit(st.session_state["distance_unit_selector"])
        if uploaded_distance is not None:
            st.session_state["distance_input_value"] = float(uploaded_distance)

st.markdown("### Runner Profile")
runner_col1, runner_col2 = st.columns(2)
with runner_col1:
    age = st.number_input("Age", min_value=1, max_value=120, value=50, step=1)
with runner_col2:
    gender = st.selectbox("Gender", ["male", "female"])

with st.container(border=True):
    st.markdown("### Run Details")
    run_detail_col1, run_detail_col2 = st.columns(2)
    with run_detail_col1:
        unit = st.selectbox(
            "Distance unit",
            ["miles", "km"],
            key="distance_unit_selector",
            on_change=sync_distance_on_unit_change,
        )
    with run_detail_col2:
        distance_preset = st.selectbox(
            "Distance preset",
            preset_options,
            key="distance_preset_selector",
            on_change=sync_distance_from_preset,
        )

    distance_input = st.number_input(
        f"Distance ({unit})",
        min_value=0.1,
        step=0.1,
        key="distance_input_value",
    )

    st.markdown("#### Time")
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

    if use_weather or use_grade or use_altitude:
        st.markdown("### Adjustment Details")

    if use_weather:
        st.caption("Enter temperature, humidity, or both. If you enter both, the app uses them together to estimate the weather effect.")

        weather_col1, weather_col2 = st.columns(2)
        with weather_col1:
            use_temperature_input = st.checkbox("Use temperature", value=True)
            temperature_f = st.number_input(
                "Temperature (°F)",
                min_value=-20.0,
                max_value=130.0,
                value=60.0,
                step=1.0
            )
        with weather_col2:
            use_humidity_input = st.checkbox("Use humidity", value=False)
            humidity = st.number_input(
                "Humidity (%)",
                min_value=0.0,
                max_value=100.0,
                value=50.0,
                step=1.0
            )
    else:
        use_temperature_input = False
        use_humidity_input = False
        temperature_f = None
        humidity = None

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

        elevation_col1, elevation_col2 = st.columns(2)
        with elevation_col1:
            elevation_gain = st.number_input(
                f"Elevation gain ({elevation_unit})",
                min_value=0.0,
                value=float(default_gain),
                step=10.0,
            )
        with elevation_col2:
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

    if use_altitude:
        altitude_ft = st.number_input(
            "Average altitude (feet above sea level)",
            min_value=0.0,
            value=0.0,
            step=100.0,
        )
    else:
        altitude_ft = 0.0

    submitted = st.button("Calculate")

if submitted:
    raw_time_minutes = hours * 60 + minutes + seconds / 60

    if distance_input <= 0:
        st.error("Distance must be greater than zero.")
    elif raw_time_minutes <= 0:
        st.error("Time must be greater than zero.")
    else:
        raw_pace = raw_time_minutes / distance_input

        # Count selected adjustment dimensions. Weather only counts if at least one
        # weather input is selected.
        weather_selected = use_weather and (use_temperature_input or use_humidity_input)
        selected_adjustment_count = sum([use_age, weather_selected, use_grade, use_altitude])

        # Calculate weather inputs once so the combined result and individual estimate
        # use the same weather assumptions.
        condition_result = None
        weather_temperature_f = None
        weather_humidity = None
        weather_basis = None

        if use_weather:
            weather_temperature_f = temperature_f if use_temperature_input else None
            weather_humidity = humidity if use_humidity_input else None
            condition_result = calculate_weather_factor(weather_temperature_f, weather_humidity)

            if weather_temperature_f is not None and weather_humidity is not None:
                weather_basis = "combined heat and humidity effects"
            elif weather_temperature_f is not None:
                weather_basis = "temperature effects"
            elif weather_humidity is not None:
                weather_basis = "humidity effects on cooling"

        # Calculate hill factor once so the combined result and individual estimate
        # use the same hill assumptions.
        hill_method = None
        hill_factor = None
        if use_grade:
            if activity_data and activity_data.get("source_format") == "GPX" and activity_data.get("segments"):
                hill_factor = calculate_grade_factor_from_segments(activity_data["segments"])
                hill_method = "rolling segment GPX model"
            else:
                hill_factor = calculate_grade_factor_from_totals(
                    distance_value=distance_input,
                    unit=unit,
                    elevation_gain=elevation_gain,
                    elevation_loss=elevation_loss,
                    elev_unit=elevation_unit,
                )
                hill_method = "total gain/loss estimate"

        altitude_factor = calculate_altitude_factor(altitude_ft) if use_altitude else None

        # Combined adjustment path. Weather, hills, and altitude are applied first.
        # Age, when selected, is applied last to the already-adjusted time.
        combined_time = raw_time_minutes
        applied_adjustments = []
        combined_messages = []

        if use_weather:
            if condition_result is None:
                combined_messages.append(("info", "Weather adjustment was selected, but no temperature or humidity value was selected."))
            else:
                combined_time *= condition_result["factor"]
                applied_adjustments.append("weather")

        if use_grade and hill_factor is not None:
            combined_time /= hill_factor
            applied_adjustments.append("hills")

        if use_altitude and altitude_factor is not None:
            combined_time *= altitude_factor
            applied_adjustments.append("altitude")

        combined_age_result = None
        if use_age:
            try:
                combined_age_result = calculate_age_grading(
                    actual_minutes=combined_time,
                    age=int(age),
                    gender=gender,
                    distance_value=distance_input,
                    unit=unit,
                )
                if combined_age_result.get("applied"):
                    combined_time = combined_age_result["age_adjusted_minutes"]
                    applied_adjustments.append("age")
                else:
                    combined_messages.append(("info", combined_age_result.get("reason", "Age adjustment was not applied.")))
            except FileNotFoundError as exc:
                combined_messages.append(("error", str(exc)))
            except Exception as exc:
                combined_messages.append(("error", f"Could not calculate age adjustment: {exc}"))

        combined_pace = combined_time / distance_input

        # Individual adjustment estimates. Each selected adjustment is shown on its own,
        # starting from the actual pace. The order follows the toggle order.
        individual_messages = []

        if use_age:
            try:
                age_result = calculate_age_grading(
                    actual_minutes=raw_time_minutes,
                    age=int(age),
                    gender=gender,
                    distance_value=distance_input,
                    unit=unit,
                )
                if age_result.get("applied"):
                    age_adjusted_pace = age_result["age_adjusted_minutes"] / distance_input
                    age_grade_pct = age_result["age_grade_pct"] * 100
                    near_peak_age_note = ""
                    if age_result.get("age_adjusted_pace_close_to_actual"):
                        near_peak_age_note = " Because you are near the peak-age range, the age-adjusted pace is very close to your actual pace."

                    individual_messages.append((
                        "write",
                        f"**Age:** Your performance is equivalent to a pace of "
                        f"{format_pace(age_adjusted_pace, unit)} for a peak-age runner of similar ability, "
                        f"based on standard age-grading tables.{near_peak_age_note}"
                    ))
                    individual_messages.append((
                        "write",
                        f"This corresponds to an age grade of {age_grade_pct:.1f}%. Age grade compares your time "
                        f"with standard top performances for your age, gender, and distance."
                    ))
                    individual_messages.append((
                        "write",
                        f"This is considered a **{age_result['age_grade_label']}** performance for your age."
                    ))
                    if age_result["interpolated"]:
                        individual_messages.append((
                            "caption",
                            f"Age standards were interpolated between "
                            f"{age_result['lower_distance_km']:.2f} km and "
                            f"{age_result['upper_distance_km']:.2f} km."
                        ))
                    individual_messages.append(("expander", "age"))
                else:
                    individual_messages.append(("info", age_result.get("reason", "Age adjustment was not applied.")))
                    individual_messages.append(("expander", "age"))
            except FileNotFoundError as exc:
                individual_messages.append(("error", str(exc)))
            except Exception as exc:
                individual_messages.append(("error", f"Could not calculate age adjustment: {exc}"))

        if use_weather:
            if condition_result is None:
                individual_messages.append(("info", "Weather adjustment was selected, but no temperature or humidity value was selected."))
            else:
                weather_pace = (raw_time_minutes * condition_result["factor"]) / distance_input
                individual_messages.append((
                    "write",
                    f"**Weather:** Your equivalent pace in moderate weather would have been "
                    f"{format_pace(weather_pace, unit)}, based on {weather_basis}."
                ))
                individual_messages.append((
                    "caption",
                    "This adjustment estimates how weather may have affected your pace and should be treated as an approximation rather than a precise physiological model."
                ))
                if condition_result["details"]:
                    individual_messages.append(("caption", f"Weather inputs used: {condition_result['details']}."))
                individual_messages.append(("expander", "weather"))

        if use_grade:
            if hill_factor is not None:
                hill_pace = (raw_time_minutes / hill_factor) / distance_input
                individual_messages.append((
                    "write",
                    f"**Hills:** Your equivalent pace on a flat course would have been "
                    f"{format_pace(hill_pace, unit)}, based on grade-adjusted effort."
                ))
                individual_messages.append(("caption", f"Hill method used: {hill_method}."))
                individual_messages.append((
                    "caption",
                    "This hill adjustment uses your route’s elevation profile to estimate an equivalent flat-course pace and may differ from other methods such as Strava GAP."
                ))
                individual_messages.append(("expander", "hills"))

        if use_altitude:
            altitude_pace = (raw_time_minutes * altitude_factor) / distance_input
            individual_messages.append((
                "write",
                f"**Altitude:** Your equivalent pace at sea level would have been "
                f"{format_pace(altitude_pace, unit)}, based on altitude effects on oxygen availability."
            ))
            individual_messages.append(("expander", "altitude"))

        st.subheader("Results")
        st.write(f"**Actual pace:** {format_pace(raw_pace, unit)}")

        if selected_adjustment_count > 1:
            st.write(f"**Combined adjusted pace:** {format_pace(combined_pace, unit)}")
            if use_age:
                st.caption(
                    "This combined pace applies the selected weather, hill, and/or altitude adjustments first, "
                    "then applies the age adjustment to estimate the peak-age equivalent performance."
                )
            else:
                st.caption(
                    "This combined pace applies the selected adjustments together to estimate the equivalent pace "
                    "under the selected reference conditions."
                )
        else:
            st.write(f"**Adjusted pace:** {format_pace(combined_pace, unit)}")

        for message_type, message in combined_messages:
            if message_type == "info":
                st.info(message)
            elif message_type == "error":
                st.error(message)

        if selected_adjustment_count > 1 and individual_messages:
            st.markdown("### Individual adjustment estimates")

        for message_type, message in individual_messages:
            if message_type == "write":
                st.write(message)
            elif message_type == "caption":
                st.caption(message)
            elif message_type == "info":
                st.info(message)
            elif message_type == "error":
                st.error(message)
            elif message_type == "expander":
                render_how_estimate_works(message)

        if applied_adjustments:
            adjustment_order = [
                ("age", "Age"),
                ("weather", "Weather"),
                ("hills", "hills"),
                ("altitude", "altitude"),
            ]
            applied_display = [label for key, label in adjustment_order if key in applied_adjustments]
            st.write("**Adjustments applied:** " + ", ".join(applied_display))
        else:
            st.write("**Adjustments applied:** none")

        improvement = raw_time_minutes - combined_time
        if improvement > 0:
            percent = improvement / raw_time_minutes * 100
            st.success(f"Your adjusted pace is {percent:.1f}% faster than your actual pace.")
        elif improvement < 0:
            percent = abs(improvement) / raw_time_minutes * 100
            st.info(f"Your adjusted pace is {percent:.1f}% slower than your actual pace.")
        else:
            st.info("No change from the selected adjustments.")

st.caption("Carbon footprint of running this app: ~0.01 miles driven by a gasoline-powered car.")

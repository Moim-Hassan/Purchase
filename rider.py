import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import json
import os
import qrcode
import io
import math
import requests
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image

st.set_page_config(layout="wide", page_title="Location Tracker & QR Scanner", page_icon="📍")

DATA_FILE = "tracker_data.json"

# ── GPS Bridge Server ───────────────────────────────────────────────────────
# A tiny HTTP server that accepts GPS coords from the browser JS
# and stores them in memory for the Python side to read.

_latest_gps = {"lat": None, "lon": None, "error": None}
_gps_lock = threading.Lock()

class _GPSBridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            with _gps_lock:
                if "error" in data:
                    _latest_gps["error"] = data["error"]
                elif "lat" in data and "lon" in data:
                    _latest_gps["lat"] = data["lat"]
                    _latest_gps["lon"] = data["lon"]
                    _latest_gps["error"] = None
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass

def _start_gps_bridge():
    try:
        server = HTTPServer(("127.0.0.1", 0), _GPSBridgeHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return port
    except Exception:
        return None

_gps_port = _start_gps_bridge()

def _read_gps():
    with _gps_lock:
        return _latest_gps["lat"], _latest_gps["lon"], _latest_gps["error"]

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"locations": [], "reports": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

if "data" not in st.session_state:
    st.session_state.data = load_data()

if "user_lat" not in st.session_state:
    st.session_state.user_lat = None
if "user_lon" not in st.session_state:
    st.session_state.user_lon = None
if "nearest_location_idx" not in st.session_state:
    st.session_state.nearest_location_idx = None
if "route_data" not in st.session_state:
    st.session_state.route_data = None
if "route_order" not in st.session_state:
    st.session_state.route_order = None

# ── Helpers ──────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def osrm_route(coords):
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson&steps=true"
    try:
        r = requests.get(url, timeout=100)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def get_directions_text(steps):
    directions = []
    for i, step in enumerate(steps):
        maneuver = step.get("maneuver", {})
        modifier = maneuver.get("modifier", "")
        ttype = maneuver.get("type", "")
        name = step.get("name", "")
        dist = step.get("distance", 0)
        instruction = ttype.replace("-", " ").title()
        if modifier:
            instruction = f"{modifier.title()} onto {name}" if name else modifier.title()
        elif name:
            instruction = f"Continue on {name}"
        if dist > 0:
            if dist >= 1000:
                instruction += f" for {dist / 1000:.1f} km"
            else:
                instruction += f" for {int(dist)} m"
        directions.append(instruction)
    return directions

def generate_qr_image(data_str):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(data_str)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def find_nearest(user_lat, user_lon, locations):
    min_dist = float("inf")
    min_idx = None
    for i, loc in enumerate(locations):
        d = haversine(user_lat, user_lon, loc["lat"], loc["lon"])
        if d < min_dist:
            min_dist = d
            min_idx = i
    return min_idx, min_dist

# ── GPS TRACKING COMPONENT ──────────────────────────────────────────────────

def _gps_html(port):
    return f"""
<div style="padding:10px; background:#1a1a2e; border-radius:8px; color:#e0e0e0; font-family:monospace;">
<b style="color:#00ff88;">&#128225; LIVE GPS TRACKING ACTIVE</b><br>
<span id="gps-status" style="color:#aaa;">Waiting for GPS data...</span>
</div>
<script>
(function() {{
    var status = document.getElementById("gps-status");
    if (!navigator.geolocation) {{
        status.innerText = "Geolocation not supported by your browser";
        return;
    }}
    status.innerText = "Requesting location access...";
    navigator.geolocation.watchPosition(
        function(pos) {{
            var lat = pos.coords.latitude;
            var lon = pos.coords.longitude;
            status.innerText = lat.toFixed(6) + ", " + lon.toFixed(6);
            fetch("http://127.0.0.1:{port}/", {{
                method: "POST",
                headers: {{"Content-Type": "application/json"}},
                body: JSON.stringify({{lat: lat, lon: lon}})
            }});
        }},
        function(err) {{
            status.innerText = "GPS Error: " + err.message + " (allow location access)";
            fetch("http://127.0.0.1:{port}/", {{
                method: "POST",
                headers: {{"Content-Type": "application/json"}},
                body: JSON.stringify({{error: err.message}})
            }});
        }},
        {{enableHighAccuracy: true, maximumAge: 5000, timeout: 15000}}
    );
}})();
</script>
"""

# ── QR SCANNER COMPONENT ────────────────────────────────────────────────────

def qr_scanner_html(location_id):
    return f"""
    <div id="qr-reader" style="width:100%; max-width:400px;"></div>
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    <script>
    var lastResult = "";
    var scanner = new Html5Qrcode("qr-reader");
    scanner.start(
        {{facingMode: "environment"}},
        {{fps: 10, qrbox: 250}},
        function onScanSuccess(decodedText) {{
            if (decodedText !== lastResult) {{
                lastResult = decodedText;
                window.parent.postMessage(
                    {{type: "streamlit:setComponentValue", value: decodedText}},
                    "*"
                );
            }}
        }},
        function onScanFailure() {{}}
    );
    </script>
    """

# ── SIDEBAR / PANEL SELECTOR ────────────────────────────────────────────────

st.sidebar.title("📍 Navigation")
panel = st.sidebar.radio("Select Panel", ["👤 User Panel", "🔧 Admin Panel"])

# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

if panel == "🔧 Admin Panel":
    st.title("🔧 Admin Panel")
    tab1, tab2, tab3 = st.tabs(["📍 Manage Locations", "🎫 QR Codes", "📋 Reports"])

    with tab1:
        st.subheader("Add / Edit Locations")

        with st.form("add_location"):
            col1, col2 = st.columns(2)
            with col1:
                loc_name = st.text_input("Location Name")
                loc_lat = st.number_input("Latitude", value=0.0, format="%.6f")
            with col2:
                loc_id = st.text_input("Location ID (unique)")
                loc_lon = st.number_input("Longitude", value=0.0, format="%.6f")

            st.markdown("**Predefined Issues** (one per line)")
            issues_text = st.text_area("Issues", placeholder="Broken streetlight\nPothole\nGraffiti\nDamaged sign", height=120)

            submitted = st.form_submit_button("➕ Add Location", type="primary")

            if submitted:
                if not loc_name or not loc_id:
                    st.error("Name and ID are required.")
                elif any(loc["id"] == loc_id for loc in st.session_state.data["locations"]):
                    st.error("Location ID already exists.")
                else:
                    issues = [i.strip() for i in issues_text.split("\n") if i.strip()]
                    st.session_state.data["locations"].append({
                        "id": loc_id,
                        "name": loc_name,
                        "lat": loc_lat,
                        "lon": loc_lon,
                        "issues": issues
                    })
                    save_data(st.session_state.data)
                    st.success(f"Location '{loc_name}' added!")
                    st.rerun()

        st.subheader("Existing Locations")
        if st.session_state.data["locations"]:
            for i, loc in enumerate(st.session_state.data["locations"]):
                with st.expander(f"📍 {loc['name']} ({loc['id']})"):
                    st.write(f"**Coordinates:** {loc['lat']}, {loc['lon']}")
                    st.write(f"**Issues:** {', '.join(loc['issues']) if loc['issues'] else 'None'}")
                    if st.button(f"🗑️ Delete {loc['id']}", key=f"del_{i}"):
                        st.session_state.data["locations"].pop(i)
                        save_data(st.session_state.data)
                        st.rerun()
        else:
            st.info("No locations added yet.")

    with tab2:
        st.subheader("Generated QR Codes")
        if st.session_state.data["locations"]:
            for loc in st.session_state.data["locations"]:
                with st.expander(f"QR: {loc['name']}"):
                    qr_data = json.dumps({"type": "location", "id": loc["id"], "name": loc["name"], "lat": loc["lat"], "lon": loc["lon"]})
                    qr_buf = generate_qr_image(qr_data)
                    st.image(qr_buf, caption=f"{loc['name']} ({loc['id']})", width=250)
                    st.code(qr_data, language=None)
                    st.download_button(
                        label="⬇️ Download QR",
                        data=qr_buf.getvalue(),
                        file_name=f"qr_{loc['id']}.png",
                        mime="image/png",
                        key=f"dl_{loc['id']}"
                    )
        else:
            st.info("Add locations first to generate QR codes.")

    with tab3:
        st.subheader("Submitted Reports")
        if st.session_state.data["reports"]:
            reports_df = pd.DataFrame(st.session_state.data["reports"])
            st.dataframe(reports_df, use_container_width=True)
        else:
            st.info("No reports submitted yet.")

# ══════════════════════════════════════════════════════════════════════════════
#  USER PANEL
# ══════════════════════════════════════════════════════════════════════════════

elif panel == "👤 User Panel":
    st.title("👤 User Panel")

    locations = st.session_state.data["locations"]

    if not locations:
        st.warning("No locations available. Ask the admin to add some first.")
        st.stop()

    # ── GPS Tracker ──────────────────────────────────────────────────────
    st.subheader("📡 Live Location Tracker")

    if _gps_port:
        gps_col, status_col = st.columns([1, 2])
        with gps_col:
            components.html(_gps_html(_gps_port), height=80)
        with status_col:
            lat, lon, gps_error = _read_gps()
            if lat is not None:
                st.session_state.user_lat = lat
                st.session_state.user_lon = lon
                st.success(f"📍 Your Location: {lat:.6f}, {lon:.6f}")
            elif st.session_state.user_lat is not None:
                st.success(f"📍 Your Location: {st.session_state.user_lat:.6f}, {st.session_state.user_lon:.6f}")
            elif gps_error:
                st.error(f"GPS Error: {gps_error}")
            else:
                st.info("Waiting for GPS coordinates... Allow location access in your browser.")
                if "gps_attempts" not in st.session_state:
                    st.session_state.gps_attempts = 0
                st.session_state.gps_attempts += 1
                if st.session_state.gps_attempts < 15:
                    time.sleep(2)
                    st.rerun()
                else:
                    st.warning("GPS acquisition timed out. Please check your browser settings and reload.")
    else:
        st.error("GPS bridge server failed to start. Check port availability.")

    st.divider()

    # ── Proximity Detection ──────────────────────────────────────────────
    if st.session_state.user_lat is not None:
        nearest_idx, nearest_dist = find_nearest(
            st.session_state.user_lat, st.session_state.user_lon, locations
        )
        nearest = locations[nearest_idx]
        st.session_state.nearest_location_idx = nearest_idx

        st.subheader("🔍 Nearest Location")
        proximity_col, info_col = st.columns(2)
        with proximity_col:
            st.metric("Nearest Location", nearest["name"], f"{nearest_dist:.0f} m away")
        with info_col:
            if nearest_dist <= 50:
                st.success("✅ You are within range! QR Scanner unlocked.")
            elif nearest_dist <= 200:
                st.warning(f"⚠️ {nearest_dist:.0f}m away. Get closer to unlock QR scanner.")
            else:
                st.info(f"📍 {nearest_dist:.0f}m away.")

    # ── Route Calculation ────────────────────────────────────────────────
    st.divider()
    st.subheader("🗺️ Route & Directions")

    if st.session_state.user_lat is not None:
        if st.button("🚗 Calculate Shortest Route", type="primary"):
            with st.spinner("Calculating optimal driving route..."):
                start = [st.session_state.user_lat, st.session_state.user_lon]
                dests = [[loc["lat"], loc["lon"]] for loc in locations]

                all_coords = [start] + dests
                result = osrm_route(all_coords)

                if result and result.get("code") == "Ok":
                    route = result["routes"][0]
                    leg_dist = route.get("distance", 0)
                    leg_time = route.get("duration", 0)

                    waypoint_order = []
                    for leg_idx in range(len(locations)):
                        wp = result["waypoints"][leg_idx + 1]
                        wp_index = wp.get("waypoint_index", leg_idx + 1)
                        waypoint_order.append(leg_idx)

                    st.session_state.route_data = {
                        "geometry": route["geometry"]["coordinates"],
                        "distance": leg_dist,
                        "duration": leg_time,
                        "legs": route.get("legs", [])
                    }
                    st.session_state.route_order = waypoint_order
                else:
                    st.error("Could not calculate route. Try again later.")

    # ── Display Map & Directions ─────────────────────────────────────────
    try:
        import folium
        from streamlit_folium import st_folium
        HAS_FOLIUM = True
    except ImportError:
        HAS_FOLIUM = False

    if HAS_FOLIUM and st.session_state.user_lat is not None:
        m = folium.Map(
            location=[st.session_state.user_lat, st.session_state.user_lon],
            zoom_start=14
        )

        folium.Marker(
            [st.session_state.user_lat, st.session_state.user_lon],
            tooltip="Your Location",
            icon=folium.Icon(color="red", icon="user", prefix="fa")
        ).add_to(m)

        for i, loc in enumerate(locations):
            is_nearest = (i == st.session_state.nearest_location_idx)
            color = "green" if is_nearest else "blue"
            icon_name = "check-circle" if is_nearest else "map-marker"
            folium.Marker(
                [loc["lat"], loc["lon"]],
                tooltip=f"{loc['name']} {'(NEAREST)' if is_nearest else ''}",
                icon=folium.Icon(color=color, icon=icon_name, prefix="fa")
            ).add_to(m)

        if st.session_state.route_data:
            route_coords = [[c[1], c[0]] for c in st.session_state.route_data["geometry"]]
            folium.PolyLine(route_coords, color="#0066ff", weight=5, opacity=0.8).add_to(m)

            dist_km = st.session_state.route_data["distance"] / 1000
            dur_min = st.session_state.route_data["duration"] / 60
            st.info(f"🚗 Total Route: **{dist_km:.1f} km** | ⏱️ Estimated Time: **{dur_min:.0f} min**")

        map_key = f"user_map_{st.session_state.user_lat}_{st.session_state.user_lon}"
        if st.session_state.route_data:
            map_key += f"_route_{st.session_state.route_data['distance']}"
        st_folium(m, width=1100, height=500, key=map_key)

    # ── Directions List ──────────────────────────────────────────────────
    if st.session_state.route_data and st.session_state.route_data.get("legs"):
        with st.expander("📋 Turn-by-Turn Directions", expanded=True):
            for leg_idx, leg in enumerate(st.session_state.route_data["legs"]):
                loc_name = locations[leg_idx]["name"] if leg_idx < len(locations) else "Destination"
                st.markdown(f"**➡️ To: {loc_name}** ({leg['distance'] / 1000:.1f} km)")
                steps = []
                for step_data in leg.get("steps", []):
                    steps.extend(step_data.get("steps", [step_data]) if "steps" in step_data else [step_data])
                directions = get_directions_text(leg.get("steps", []))
                for d_idx, direction in enumerate(directions):
                    st.write(f"  {d_idx + 1}. {direction}")

    # ── QR Scanner & Issue Reporting ─────────────────────────────────────
    st.divider()
    st.subheader("📷 QR Code Scanner")

    if st.session_state.user_lat is not None:
        nearest_idx, nearest_dist = find_nearest(
            st.session_state.user_lat, st.session_state.user_lon, locations
        )

        if nearest_dist <= 50:
            st.success(f"✅ In range of **{locations[nearest_idx]['name']}** — Scanner unlocked!")
            scanned_result = components.html(
                qr_scanner_html(locations[nearest_idx]["id"]),
                height=350
            )

            if scanned_result:
                try:
                    scanned_data = json.loads(scanned_result)
                    if scanned_data.get("type") == "location":
                        scanned_loc = None
                        for loc in locations:
                            if loc["id"] == scanned_data.get("id"):
                                scanned_loc = loc
                                break

                        if scanned_loc:
                            st.success(f"📱 Scanned: **{scanned_loc['name']}**")

                            st.markdown("**Select Issues to Report:**")
                            if scanned_loc.get("issues"):
                                selected_issues = st.multiselect(
                                    "Issues found at this location",
                                    options=scanned_loc["issues"],
                                    key="issue_select"
                                )
                            else:
                                selected_issues = []
                                st.info("No predefined issues for this location.")

                            additional_notes = st.text_area("Additional Notes (optional)", key="notes_input")

                            if st.button("📤 Submit Report", type="primary"):
                                report = {
                                    "location_id": scanned_loc["id"],
                                    "location_name": scanned_loc["name"],
                                    "lat": st.session_state.user_lat,
                                    "lon": st.session_state.user_lon,
                                    "issues": selected_issues,
                                    "notes": additional_notes,
                                    "timestamp": pd.Timestamp.now().isoformat()
                                }
                                st.session_state.data["reports"].append(report)
                                save_data(st.session_state.data)
                                st.success("✅ Report submitted successfully!")
                                st.rerun()
                        else:
                            st.warning("QR code does not match any known location.")
                except (json.JSONDecodeError, TypeError):
                    pass
        else:
            st.warning(f"🔒 Scanner locked. Move **{nearest_dist - 50:.0f}m closer** to **{locations[nearest_idx]['name']}** to unlock.")
    else:
        st.info("Enable GPS tracking to use the QR scanner.")

    # ── Location Status Overview ─────────────────────────────────────────
    st.divider()
    st.subheader("📍 All Locations Status")

    for i, loc in enumerate(locations):
        status = "🟢 Nearest" if i == st.session_state.nearest_location_idx else "🔵"
        col_name, col_dist = st.columns([2, 1])
        with col_name:
            st.write(f"{status} **{loc['name']}** ({loc['id']})")
        with col_dist:
            if st.session_state.user_lat is not None:
                d = haversine(st.session_state.user_lat, st.session_state.user_lon, loc["lat"], loc["lon"])
                st.write(f"{d:.0f} m")
            else:
                st.write("—")

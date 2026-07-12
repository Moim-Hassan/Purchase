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
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from PIL import Image

st.set_page_config(layout="wide", page_title="Rider App - Route & QR Scanner", page_icon="📍")

DATA_FILE = "tracker_data.json"

# ── GPS COMPONENT SERVER ────────────────────────────────────────────────────

_COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "gps")

if not hasattr(sys, "_gps_component_port"):
    sys._gps_component_port = None

    class _CompHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_COMPONENT_DIR, **kw)
        def log_message(self, *a):
            pass

    try:
        _srv = HTTPServer(("127.0.0.1", 0), _CompHandler)
        sys._gps_component_port = _srv.server_address[1]
        threading.Thread(target=_srv.serve_forever, daemon=True).start()
    except Exception:
        pass

if sys._gps_component_port:
    _gps_component = components.declare_component(
        "gps_component",
        url=f"http://127.0.0.1:{sys._gps_component_port}",
    )
else:
    _gps_component = None

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
if "route_order" not in st.session_state:
    st.session_state.route_order = None
if "route_geometry" not in st.session_state:
    st.session_state.route_geometry = None
if "route_distance" not in st.session_state:
    st.session_state.route_distance = None
if "route_duration" not in st.session_state:
    st.session_state.route_duration = None
if "route_legs" not in st.session_state:
    st.session_state.route_legs = None
if "visited_indices" not in st.session_state:
    st.session_state.visited_indices = set()
if "current_target_idx" not in st.session_state:
    st.session_state.current_target_idx = None
if "last_scan_result" not in st.session_state:
    st.session_state.last_scan_result = None

# ── Helpers ──────────────────────────────────────────────────────────────────

DEFAULT_ISSUES = [
    "Company closed",
    "Product not available",
    "Store not found",
    "Wrong location",
    "Out of stock",
    "Address not found",
    "No response",
    "Damaged goods",
    "Other"
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def nearest_neighbor_tsp(start, destinations):
    n = len(destinations)
    if n == 0:
        return []
    if n == 1:
        return [0]

    dists = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                dists[i][j] = haversine(
                    destinations[i][0], destinations[i][1],
                    destinations[j][0], destinations[j][1]
                )

    start_dists = [
        haversine(start[0], start[1], destinations[i][0], destinations[i][1])
        for i in range(n)
    ]

    visited = [False] * n
    order = []

    first = min(range(n), key=lambda i: start_dists[i])
    visited[first] = True
    order.append(first)

    current = first
    for _ in range(n - 1):
        next_idx = min(
            (i for i in range(n) if not visited[i]),
            key=lambda i: dists[current][i]
        )
        visited[next_idx] = True
        order.append(next_idx)
        current = next_idx

    return order

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
    for step in steps:
        maneuver = step.get("maneuver", {})
        modifier = maneuver.get("modifier", "")
        ttype = maneuver.get("type", "")
        name = step.get("name", "")
        dist = step.get("distance", 0)
        if ttype == "turn" or ttype == "new name":
            instruction = f"Turn {modifier} onto {name}" if name else f"Turn {modifier}"
        elif ttype == "depart":
            instruction = "Start"
        elif ttype == "arrive":
            instruction = "Arrive at destination"
        elif ttype == "roundabout":
            instruction = "Enter roundabout and take exit"
        elif ttype == "end of road":
            instruction = f"Turn {modifier}" if modifier else "Turn"
        else:
            instruction = f"Continue on {name}" if name else "Continue"
        if dist > 0:
            if dist >= 1000:
                instruction += f" ({dist/1000:.1f} km)"
            else:
                instruction += f" ({int(dist)} m)"
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

def get_qr_value(loc):
    return loc.get("qr_value") or json.dumps({
        "type": "location", "id": loc["id"],
        "name": loc["name"], "lat": loc["lat"], "lon": loc["lon"]
    })

def find_nearest(user_lat, user_lon, locations):
    min_dist = float("inf")
    min_idx = None
    for i, loc in enumerate(locations):
        d = haversine(user_lat, user_lon, loc["lat"], loc["lon"])
        if d < min_dist:
            min_dist = d
            min_idx = i
    return min_idx, min_dist

def qr_scanner_html():
    return """
    <div id="qr-reader" style="width:100%; max-width:400px; margin:0 auto;"></div>
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    <script>
    var lastResult = "";
    var scanner = new Html5Qrcode("qr-reader");
    scanner.start(
        {facingMode: "environment"},
        {fps: 10, qrbox: 250},
        function onScanSuccess(decodedText) {
            if (decodedText !== lastResult) {
                lastResult = decodedText;
                window.parent.postMessage(
                    {type: "streamlit:setComponentValue", value: decodedText},
                    "*"
                );
            }
        },
        function onScanFailure() {}
    );
    </script>
    """

# ── SIDEBAR ────────────────────────────────────────────────────────────────

st.sidebar.title("📍 Rider App")
panel = st.sidebar.radio("Select Panel", ["👤 User Panel", "🔧 Admin Panel"])

# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

if panel == "🔧 Admin Panel":
    st.title("🔧 Admin Panel")
    tab1, tab2, tab3 = st.tabs(["📍 Manage Locations", "🎫 QR Codes", "📋 Reports"])

    with tab1:
        st.subheader("Add New Location")
        with st.form("add_location"):
            col1, col2 = st.columns(2)
            with col1:
                loc_name = st.text_input("Location Name *")
                loc_lat = st.number_input("Latitude *", value=23.73, format="%.6f")
                qr_value = st.text_input("QR Code Custom Value")
            with col2:
                loc_id = st.text_input("Location ID (unique) *")
                loc_lon = st.number_input("Longitude *", value=90.42, format="%.6f")

            st.caption("QR Code Value: leave empty to auto-generate from location data, or enter a custom value/link.")

            issues_text = st.text_area(
                "Predefined Issues (one per line)",
                value="\n".join(DEFAULT_ISSUES),
                height=150
            )

            submitted = st.form_submit_button("➕ Add Location", type="primary")
            if submitted:
                if not loc_name or not loc_id:
                    st.error("Name and ID are required.")
                elif any(l["id"] == loc_id for l in st.session_state.data["locations"]):
                    st.error("Location ID already exists.")
                else:
                    issues = [i.strip() for i in issues_text.split("\n") if i.strip()]
                    final_qr = qr_value if qr_value else json.dumps({
                        "type": "location", "id": loc_id,
                        "name": loc_name, "lat": loc_lat, "lon": loc_lon
                    })
                    st.session_state.data["locations"].append({
                        "id": loc_id, "name": loc_name,
                        "lat": loc_lat, "lon": loc_lon,
                        "qr_value": final_qr,
                        "issues": issues
                    })
                    save_data(st.session_state.data)
                    st.success(f"Location '{loc_name}' added!")
                    st.rerun()

        st.divider()
        st.subheader("Existing Locations")
        if st.session_state.data["locations"]:
            for i, loc in enumerate(st.session_state.data["locations"]):
                with st.expander(f"📍 {loc['name']} ({loc['id']})"):
                    st.write(f"**Coordinates:** {loc['lat']}, {loc['lon']}")
                    st.write(f"**QR Value:** `{get_qr_value(loc)}`")
                    st.write(f"**Issues:** {', '.join(loc['issues']) if loc['issues'] else 'None'}")
                    if st.button(f"🗑️ Delete", key=f"del_{i}"):
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
                    qr_data = get_qr_value(loc)
                    qr_buf = generate_qr_image(qr_data)
                    st.image(qr_buf, caption=f"{loc['name']} ({loc['id']})", width=250)
                    st.code(qr_data, language=None)
                    st.download_button(
                        label="⬇️ Download PNG",
                        data=qr_buf.getvalue(),
                        file_name=f"qr_{loc['id']}.png",
                        mime="image/png",
                        key=f"dl_{loc['id']}"
                    )
        else:
            st.info("Add locations first.")

    with tab3:
        st.subheader("Submitted Reports")
        if st.session_state.data["reports"]:
            df = pd.DataFrame(st.session_state.data["reports"])
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No reports yet.")

# ══════════════════════════════════════════════════════════════════════════════
#  USER PANEL
# ══════════════════════════════════════════════════════════════════════════════

elif panel == "👤 User Panel":
    st.title("👤 User Panel — Route & Report")

    locations = st.session_state.data["locations"]
    if not locations:
        st.warning("No locations available. Ask admin to add some first.")
        st.stop()

    # ── GPS Tracker ──────────────────────────────────────────────────────
    with st.container():
        gps_col, status_col = st.columns([1, 2])
        with gps_col:
            st.subheader("📡 GPS")
            if _gps_component is None:
                st.error("GPS component failed to start.")
            else:
                gps_val = _gps_component(key="gps_tracker")
        with status_col:
            st.subheader("📍 Your Position")
            if gps_val is not None:
                try:
                    gps_data = json.loads(gps_val)
                    if "error" not in gps_data:
                        st.session_state.user_lat = gps_data["lat"]
                        st.session_state.user_lon = gps_data["lon"]
                        st.success(f"**{gps_data['lat']:.6f}, {gps_data['lon']:.6f}**")
                    else:
                        st.error(f"GPS Error: {gps_data['error']}")
                except (json.JSONDecodeError, TypeError):
                    pass
            if st.session_state.user_lat is not None and gps_val is None:
                st.success(f"**{st.session_state.user_lat:.6f}, {st.session_state.user_lon:.6f}**")
            elif st.session_state.user_lat is None:
                st.info("Waiting for GPS... Allow location access in browser.")

    st.divider()

    # ── Route Calculation ────────────────────────────────────────────────
    st.subheader("🗺️ Optimal Route")

    col_calc, col_reset = st.columns([1, 1])
    with col_calc:
        calc_btn = st.button("🚗 Calculate Shortest Route", type="primary", use_container_width=True)
    with col_reset:
        if st.button("🔄 Reset Trip", use_container_width=True):
            for key in ["route_order", "route_geometry", "route_distance",
                        "route_duration", "route_legs", "visited_indices",
                        "current_target_idx", "last_scan_result"]:
                if key in ("visited_indices",):
                    st.session_state[key] = set()
                else:
                    st.session_state[key] = None
            st.rerun()

    if calc_btn:
        if st.session_state.user_lat is None:
            st.warning("⚠️ Enable GPS location first.")
        else:
            with st.spinner("🧮 Solving optimal route..."):
                start = [st.session_state.user_lat, st.session_state.user_lon]
                dests = [[loc["lat"], loc["lon"]] for loc in locations]

                optimal_order = nearest_neighbor_tsp(start, dests)
                st.session_state.route_order = optimal_order

                ordered_coords = [start] + [dests[i] for i in optimal_order]

                result = osrm_route(ordered_coords)
                if result and result.get("code") == "Ok":
                    route = result["routes"][0]
                    st.session_state.route_geometry = route["geometry"]["coordinates"]
                    st.session_state.route_distance = route.get("distance", 0)
                    st.session_state.route_duration = route.get("duration", 0)
                    st.session_state.route_legs = route.get("legs", [])
                    st.session_state.visited_indices = set()
                    st.session_state.current_target_idx = optimal_order[0] if optimal_order else None
                    st.success(f"✅ Optimal route found — {len(optimal_order)} stops")
                else:
                    st.error("Could not calculate route via OSRM. Try again later.")

    st.divider()

    # ── Map & Progress ───────────────────────────────────────────────────
    if st.session_state.route_order is not None and st.session_state.user_lat is not None:
        optimal_order = st.session_state.route_order
        remaining = [i for i in optimal_order if i not in st.session_state.visited_indices]

        # Progress bar
        total = len(optimal_order)
        done = total - len(remaining)
        st.progress(done / total, text=f"Progress: {done}/{total} stops completed")

        # Stop list
        st.markdown("**🛑 Visit Order:**")
        cols = st.columns(min(total, 4))
        for rank, idx in enumerate(optimal_order):
            loc = locations[idx]
            visited = idx in st.session_state.visited_indices
            is_current = idx == st.session_state.current_target_idx
            if visited:
                icon = "✅"
            elif is_current:
                icon = "⏺️"
            else:
                icon = f"{rank+1}."
            label = f"{icon} {loc['name']}"
            st.markdown(f"<span style='font-size:0.9em'>{label}</span>", unsafe_allow_html=True)

        st.divider()

        # Map
        try:
            import folium
            from streamlit_folium import st_folium
            HAS_FOLIUM = True
        except ImportError:
            HAS_FOLIUM = False

        if HAS_FOLIUM:
            center_lat = st.session_state.user_lat
            center_lon = st.session_state.user_lon
            m = folium.Map(location=[center_lat, center_lon], zoom_start=14)

            folium.Marker(
                [center_lat, center_lon],
                tooltip="You are here",
                icon=folium.Icon(color="red", icon="user", prefix="fa")
            ).add_to(m)

            for rank, idx in enumerate(optimal_order):
                loc = locations[idx]
                visited = idx in st.session_state.visited_indices
                is_current = idx == st.session_state.current_target_idx
                if visited:
                    color = "green"
                    icon_name = "check-circle"
                elif is_current:
                    color = "orange"
                    icon_name = "flag"
                else:
                    color = "blue"
                    icon_name = "map-marker"
                folium.Marker(
                    [loc["lat"], loc["lon"]],
                    tooltip=f"{'✅ ' if visited else ''}{loc['name']} (stop #{rank+1})",
                    icon=folium.Icon(color=color, icon=icon_name, prefix="fa")
                ).add_to(m)

            if st.session_state.route_geometry:
                route_coords = [[c[1], c[0]] for c in st.session_state.route_geometry]
                folium.PolyLine(route_coords, color="#0066ff", weight=5, opacity=0.8).add_to(m)

            if st.session_state.route_distance:
                dist_km = st.session_state.route_distance / 1000
                dur_min = st.session_state.route_duration / 60
                st.info(f"🚗 **{dist_km:.1f} km** total | ⏱️ **{dur_min:.0f} min** estimated")

            st_folium(m, width=1100, height=450, key="route_map")

    st.divider()

    # ── Proximity → QR Scan → Report ────────────────────────────────────
    st.subheader("📷 Scan & Report")

    if st.session_state.user_lat is not None:
        # Determine the current target (first unvisited in optimal order, or nearest as fallback)
        target_idx = None
        if st.session_state.route_order is not None:
            remaining = [i for i in st.session_state.route_order if i not in st.session_state.visited_indices]
            target_idx = remaining[0] if remaining else None
            st.session_state.current_target_idx = target_idx

        if target_idx is not None:
            target_loc = locations[target_idx]
            dist = haversine(
                st.session_state.user_lat, st.session_state.user_lon,
                target_loc["lat"], target_loc["lon"]
            )

            st.info(f"🎯 **Target:** {target_loc['name']} — **{dist:.0f}m** away")

            if dist <= 50:
                st.success(f"✅ You've arrived at **{target_loc['name']}** — QR scanner unlocked!")

                scanned_result = components.html(qr_scanner_html(), height=350)

                if scanned_result and scanned_result != st.session_state.last_scan_result:
                    st.session_state.last_scan_result = scanned_result
                    st.rerun()

                if st.session_state.last_scan_result:
                    scanned = st.session_state.last_scan_result
                    expected_qr = get_qr_value(target_loc)

                    st.code(f"Scanned: {scanned[:80]}{'...' if len(scanned) > 80 else ''}", language=None)

                    if scanned.strip() == expected_qr.strip():
                        st.success(f"✅ Location verified: **{target_loc['name']}**")

                        st.markdown("### Report Issues")
                        issues_list = target_loc.get("issues", DEFAULT_ISSUES)
                        selected_issues = st.multiselect("Select issues found:", issues_list, key="issue_sel")
                        additional_notes = st.text_area("Additional notes (optional)", key="notes_inp")

                        if st.button("📤 Submit Report", type="primary", use_container_width=True):
                            report = {
                                "location_id": target_loc["id"],
                                "location_name": target_loc["name"],
                                "user_lat": st.session_state.user_lat,
                                "user_lon": st.session_state.user_lon,
                                "issues": selected_issues,
                                "notes": additional_notes,
                                "qr_scanned": scanned,
                                "timestamp": pd.Timestamp.now().isoformat()
                            }
                            st.session_state.data["reports"].append(report)
                            save_data(st.session_state.data)
                            st.session_state.visited_indices.add(target_idx)
                            st.session_state.last_scan_result = None

                            still_remaining = [i for i in st.session_state.route_order
                                               if i not in st.session_state.visited_indices]
                            if still_remaining:
                                next_name = locations[still_remaining[0]]["name"]
                                st.success(f"✅ Report submitted! Next stop: **{next_name}**")
                            else:
                                st.success("🎉 **All locations visited! Trip completed!**")
                                st.balloons()
                            st.rerun()
                    else:
                        st.error("❌ QR code does NOT match this location. Try the correct QR code.")
                else:
                    st.info("Point your camera at the location's QR code.")
            else:
                need = int(dist - 50)
                st.warning(f"🔒 Scanner locked — **{need}m** closer needed to reach **{target_loc['name']}**")
        else:
            # No route calculated or all visited — use nearest location
            nearest_idx, nearest_dist = find_nearest(
                st.session_state.user_lat, st.session_state.user_lon, locations
            )
            nearest = locations[nearest_idx] if nearest_idx is not None else None

            if nearest and nearest_dist <= 50:
                st.success(f"✅ Near **{nearest['name']}** — scanner unlocked!")

                scanned_result = components.html(qr_scanner_html(), height=350)

                if scanned_result and scanned_result != st.session_state.last_scan_result:
                    st.session_state.last_scan_result = scanned_result
                    st.rerun()

                if st.session_state.last_scan_result:
                    scanned = st.session_state.last_scan_result
                    expected_qr = get_qr_value(nearest)

                    st.code(f"Scanned: {scanned[:80]}{'...' if len(scanned) > 80 else ''}", language=None)

                    if scanned.strip() == expected_qr.strip():
                        st.success(f"✅ Verified: **{nearest['name']}**")

                        issues_list = nearest.get("issues", DEFAULT_ISSUES)
                        selected_issues = st.multiselect("Select issues:", issues_list, key="issue_sel_fb")
                        notes = st.text_area("Notes", key="notes_fb")

                        if st.button("📤 Submit", type="primary"):
                            st.session_state.data["reports"].append({
                                "location_id": nearest["id"],
                                "location_name": nearest["name"],
                                "user_lat": st.session_state.user_lat,
                                "user_lon": st.session_state.user_lon,
                                "issues": selected_issues,
                                "notes": notes,
                                "qr_scanned": scanned,
                                "timestamp": pd.Timestamp.now().isoformat()
                            })
                            save_data(st.session_state.data)
                            st.session_state.last_scan_result = None
                            st.success("✅ Report submitted!")
                            st.rerun()
                    else:
                        st.error("❌ QR code mismatch.")
            elif nearest:
                st.info(f"📍 Nearest: **{nearest['name']}** ({nearest_dist:.0f}m). Calculate route to start.")
            else:
                st.info("No locations to scan.")
    else:
        st.info("Enable GPS location to use the QR scanner.")


import os
import math
import json
import base64
import time
from datetime import datetime

import requests
import streamlit as st
from streamlit_folium import st_folium
import folium

# ======================================
# Config
# ======================================
st.set_page_config(page_title="Mapillary Street Explorer", page_icon="🗺️", layout="centered")

# ======================================
# Helpers
# ======================================
def geocode_nominatim(address: str, retries=3, delay=0.8):
    """Return {lat, lon, label} or None if not found."""
    if not address:
        return None
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "MapillaryExplorer/1.0 (edu/demo)"}
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
            if data:
                lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                label = data[0].get("display_name", address)
                return {"lat": lat, "lon": lon, "label": label}
            return None
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(delay)

def _deg_for_meters(lat_deg: float, meters: float):
    """Approximate lat/lon offsets in degrees for given meters."""
    dlat = meters / 111_320.0
    dlon = dlat * math.cos(math.radians(lat_deg))
    return dlat, dlon

def _haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lon points."""
    from math import radians, sin, cos, asin, sqrt
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

MAP_FIELDS = "id,computed_geometry,thumb_1024_url,thumb_2048_url,captured_at,is_pano"

def mapillary_find_best(lat: float, lon: float, token: str,
                        radii_m=(150, 300, 600, 1200, 3000, 6000, 10000),
                        require_pano: bool = False):
    if not token or not token.startswith("MLY|"):
        return None, None

    base = "https://graph.mapillary.com/images"

    def rank_items(items):
        ranked = []
        for it in items:
            geom = (it.get("computed_geometry") or {}).get("coordinates")
            if isinstance(geom, (list, tuple)) and len(geom) == 2:
                dist = _haversine_m(lat, lon, geom[1], geom[0])
            else:
                dist = float("inf")
            ranked.append((bool(it.get("is_pano")), dist, it))
        ranked.sort(key=lambda x: (-int(x[0]), x[1]))
        return ranked

    try:
        r = requests.get(base, params={
            "access_token": token, "fields": MAP_FIELDS,
            "limit": 20, "closeto": f"{lat},{lon}"
        }, timeout=20)
        r.raise_for_status()
        items = r.json().get("data", [])
        if items:
            ranked = rank_items(items)
            if require_pano:
                panos = [t for t in ranked if t[0]]
                if panos:
                    it = panos[0][2]
                    return it.get("thumb_1024_url") or it.get("thumb_2048_url"), it
            else:
                it = ranked[0][2]
                return it.get("thumb_1024_url") or it.get("thumb_2048_url"), it
    except Exception:
        pass

    for radius in radii_m:
        dlat, dlon = _deg_for_meters(lat, radius)
        bbox = f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}"
        try:
            r = requests.get(base, params={
                "access_token": token, "fields": MAP_FIELDS,
                "limit": 50, "bbox": bbox
            }, timeout=20)
            r.raise_for_status()
            items = r.json().get("data", [])
            if not items:
                continue
            ranked = rank_items(items)
            if require_pano:
                panos = [t for t in ranked if t[0]]
                if panos:
                    it = panos[0][2]
                    return it.get("thumb_1024_url") or it.get("thumb_2048_url"), it
            else:
                it = ranked[0][2]
                return it.get("thumb_1024_url") or it.get("thumb_2048_url"), it
        except Exception:
            continue
    return None, None

def render_map(lat: float, lon: float, label: str = "", zoom: int = 18):
    m = folium.Map(location=[lat, lon], zoom_start=zoom, control_scale=True)
    folium.Marker([lat, lon], tooltip=label).add_to(m)
    return m

def _fmt_date(value) -> str:
    """Handle Mapillary date strings or timestamps safely."""
    if not value:
        return ""
    # If it's an integer (UNIX timestamp in milliseconds or seconds)
    if isinstance(value, (int, float)):
        try:
            # if >10 digits → milliseconds
            if value > 1e12:
                value /= 1000.0
            return datetime.utcfromtimestamp(value).date().isoformat()
        except Exception:
            return ""
    # If it's a string
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            return value[:10]
    # Fallback
    return str(value)

def pannellum_html_from_image_bytes(img_bytes: bytes, height_px: int = 480) -> str:
    data_uri = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("ascii")
    cfg = {
        "type": "equirectangular",
        "panorama": data_uri,
        "autoLoad": True,
        "autoRotate": -2,
        "showZoomCtrl": True,
        "hfov": 90
    }
    return f"""
    <div id="pano" style="width:100%; height:{int(height_px)}px; border-radius:10px; overflow:hidden;"></div>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pannellum/build/pannellum.css">
    <script src="https://cdn.jsdelivr.net/npm/pannellum/build/pannellum.js"></script>
    <script>
      (function(){{
        var cfg = {json.dumps(cfg)};
        function init(){{ window.pannellum && pannellum.viewer("pano", cfg); }}
        if (document.readyState === "complete") init(); else window.addEventListener("load", init);
      }})();
    </script>
    """

st.title("🗺️ Mapillary Street Explorer")
st.caption("Type an address, see a map, nearest Mapillary photo, and an inline 360° view when available.")

with st.sidebar:
    st.header("Settings")

    # Automatically load the token from Streamlit Cloud secrets (if available)
    default_token = os.getenv("MAPILLARY_TOKEN", "")

    # Use a hidden token — no user input required
    token = default_token

    # Panorama preference toggle (you can keep this for flexibility)
    pano_first = st.checkbox("Prefer panoramic images", value=True)

    st.caption("✅ Token loaded securely from Streamlit Cloud secrets.")


address = st.text_input("Address", placeholder="e.g., Eiffel Tower, Paris", key="address_input")
search = st.button("Search", type="primary", key="search_button")

# Store the last search results in session_state so Streamlit doesn't reset
if "last_result" not in st.session_state:
    st.session_state.last_result = None

# Only run search when button clicked
if search:
    if not address.strip():
        st.warning("Please enter an address.")
        st.stop()
    if not token or not token.startswith("MLY|"):
        st.error("Please provide a valid Mapillary token (starts with 'MLY|').")
        st.stop()

    geo = geocode_nominatim(address.strip())
    if not geo:
        st.error("No results from geocoder. Try a more precise address.")
        st.stop()

    # Save search results
    st.session_state.last_result = geo

# --- Display results (if any) ---
if st.session_state.last_result:
    geo = st.session_state.last_result
    lat, lon, label = geo["lat"], geo["lon"], geo["label"]

    st.subheader("📍 Location")
    st.write(label)
    st.write(f"**lat**: `{lat:.6f}` **lon**: `{lon:.6f}`")

    st.subheader("🗺️ Map")
    fmap = render_map(lat, lon, label)
    st_folium(fmap, width=700, height=500)

    st.subheader("🟢 Mapillary imagery")

    if pano_first:
        thumb, meta = mapillary_find_best(lat, lon, token, require_pano=True)
        if thumb and bool((meta or {}).get("is_pano")):
            st.success("Panoramic image found.")
        else:
            st.info("No panoramic image nearby — showing the closest available photo.")
            thumb, meta = mapillary_find_best(lat, lon, token, require_pano=False)
    else:
        thumb, meta = mapillary_find_best(lat, lon, token, require_pano=False)

    if not thumb or not isinstance(meta, dict):
        st.error("No Mapillary imagery found near this point.")
        st.stop()

    static_url = meta.get("thumb_1024_url") or thumb
    try:
        img = requests.get(static_url, timeout=20)
        img.raise_for_status()
        st.image(img.content, caption="Static preview (1024px)", use_column_width=True)
    except Exception as e:
        st.warning(f"Static image error: {e}")

    is_pano = bool(meta.get("is_pano"))
    date_str = _fmt_date(meta.get("captured_at", ""))

    if is_pano:
        pano_url = meta.get("thumb_2048_url") or static_url
        try:
            pbytes = requests.get(pano_url, timeout=30)
            pbytes.raise_for_status()
            html_block = pannellum_html_from_image_bytes(pbytes.content, height_px=480)
            st.components.v1.html(html_block, height=520, scrolling=False)
        except Exception as e:
            st.warning(f"Panorama load error (showing static only): {e}")

    pid = str(meta.get("id", ""))
    footer = f"ID: `{pid}`"
    if date_str:
        footer += f" — Captured: {date_str}"

    st.caption(footer) 

# Rider App - Route & QR Scanner

A Streamlit-based dispatch console for route optimization and QR code verification.

## Features

- **GPS Tracking**: Real-time location tracking via browser
- **QR Code Scanning**: Camera-based QR code verification at locations
- **Route Optimization**: Nearest-neighbor TSP algorithm for optimal stop ordering
- **OSRM Routing**: Turn-by-turn directions using OpenStreetMap routing
- **Admin Panel**: Manage locations, generate QR codes, view reports
- **User Panel**: Navigate routes, scan QR codes, submit issue reports
- **Mobile-optimized UI**: Responsive design with bottom tab navigation

## Tech Stack

- **Framework**: Streamlit
- **Routing**: OSRM (Open Source Routing Machine)
- **Mapping**: Folium + Streamlit-Folium
- **QR Generation**: qrcode library
- **Image Processing**: Pillow
- **Data Storage**: JSON file (`tracker_data.json`)

## Project Structure

```
rider_app/
├── app.py                  # Main application (810 lines)
├── tracker_data.json       # Location & report data
├── components/
│   ├── gps/
│   │   └── index.html     # Browser GPS component
│   └── qr/
│       └── index.html     # Camera QR scanner component
└── .streamlit/
    └── config.toml         # Dark theme configuration
```

## Installation

```bash
pip install streamlit pandas qrcode pillow requests folium streamlit-folium
```

## Usage

```bash
streamlit run app.py
```

## Panels

### Admin Panel
- Add/edit/delete locations with coordinates
- Generate and download QR codes
- View submitted reports

### User Panel
- Enable GPS tracking
- Calculate optimal route
- Navigate to stops with progress tracking
- Scan QR codes within 50m proximity
- Submit issue reports (company closed, out of stock, etc.)

## Data Format

Locations are stored in `tracker_data.json`:

```json
{
  "locations": [
    {
      "id": "1",
      "name": "Location Name",
      "lat": 23.7292,
      "lon": 90.4225,
      "qr_value": "custom-qr-data",
      "issues": ["Issue 1", "Issue 2"]
    }
  ],
  "reports": []
}
```

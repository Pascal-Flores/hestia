import json, os, sys, urllib.parse, urllib.request

UA = {"User-Agent": "HestiaAssistant/0.3 (local assistant)"}
def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=9) as response:
        return json.load(response)

def run(payload):
    city = payload["parameters"].get("city", "").strip() or os.getenv("DEFAULT_CITY", "Paris")
    geo = get("https://geocoding-api.open-meteo.com/v1/search?" +
              urllib.parse.urlencode({"name": city, "count": 1, "language": "fr", "format": "json"}))
    matches = geo.get("results", [])
    if not matches:
        return {"success": False, "error": "Ville introuvable", "city": city}
    place = matches[0]
    data = get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "timezone": "auto"
    }))
    return {"success": True, "city": place["name"], "country": place.get("country"),
            "timezone": data.get("timezone"), "current": data.get("current", {}),
            "units": data.get("current_units", {})}

try:
    print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False))
except Exception as error:
    print(json.dumps({"success": False, "error": str(error)}, ensure_ascii=False))

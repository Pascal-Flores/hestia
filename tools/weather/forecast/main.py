import json, os, sys, urllib.parse, urllib.request
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"HestiaAssistant/0.3"}), timeout=9) as r:
        return json.load(r)
def run(p):
    x=p["parameters"]
    city=x.get("city","").strip() or os.getenv("DEFAULT_CITY","Paris")
    days=max(1,min(7,int(x["days"])))
    geo=get("https://geocoding-api.open-meteo.com/v1/search?"+urllib.parse.urlencode(
      {"name":city,"count":1,"language":"fr","format":"json"}))
    places=geo.get("results",[])
    if not places:return {"success":False,"error":"Ville introuvable"}
    place=places[0]
    data=get("https://api.open-meteo.com/v1/forecast?"+urllib.parse.urlencode({
      "latitude":place["latitude"],"longitude":place["longitude"],
      "daily":"temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
      "timezone":"auto","forecast_days":days}))
    return {"success":True,"city":place["name"],"forecast":data.get("daily",{})}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

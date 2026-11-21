import json,sys,urllib.parse,urllib.request
def run(data):
    term=data["parameters"]["query"].strip()
    if not term or len(term)>150:return {"success":False,"error":"Recherche vide"}
    url="https://de1.api.radio-browser.info/json/stations/search?"+urllib.parse.urlencode({
        "name":term,"limit":6,"hidebroken":"true","order":"votes","reverse":"true"})
    req=urllib.request.Request(url,headers={"User-Agent":"Hestia/0.3"})
    with urllib.request.urlopen(req,timeout=12) as r:res=json.load(r)
    return {"success":True,"stations":[{"name":x.get("name"),"url":x.get("url_resolved"),
      "country":x.get("country"),"tags":x.get("tags")} for x in res[:6]]}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

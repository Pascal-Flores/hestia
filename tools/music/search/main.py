import json,sys,urllib.parse,urllib.request
def run(data):
    term=data["parameters"]["query"].strip()
    if not term or len(term)>250:return {"success":False,"error":"Recherche vide"}
    url="https://itunes.apple.com/search?"+urllib.parse.urlencode({
        "term":term,"media":"music","entity":"song","country":"fr","limit":5})
    req=urllib.request.Request(url,headers={"User-Agent":"Hestia/0.3"})
    with urllib.request.urlopen(req,timeout=12) as r:res=json.load(r)
    tracks=[{"title":x.get("trackName"),"artist":x.get("artistName"),
             "album":x.get("collectionName"),"url":x.get("trackViewUrl"),
             "preview":x.get("previewUrl")} for x in res.get("results",[])[:5]]
    return {"success":True,"query":term,"tracks":tracks}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

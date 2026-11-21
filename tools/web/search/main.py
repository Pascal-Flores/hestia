import json,os,sys,urllib.parse,urllib.request
def run(data):
    query=data["parameters"]["query"].strip()
    if not query or len(query)>350:return {"success":False,"error":"Requête invalide"}
    key=os.environ["BRAVE_API_KEY"]
    url="https://api.search.brave.com/res/v1/web/search?"+urllib.parse.urlencode({
      "q":query,"count":5,"country":"FR","search_lang":os.getenv("SEARCH_LANGUAGE","fr")})
    req=urllib.request.Request(url,headers={"Accept":"application/json","X-Subscription-Token":key,
        "User-Agent":"Hestia/0.3"})
    with urllib.request.urlopen(req,timeout=12) as r:body=json.load(r)
    results=[{"title":x.get("title"),"url":x.get("url"),"description":x.get("description")}
             for x in body.get("web",{}).get("results",[])[:5]]
    return {"success":True,"query":query,"results":results}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

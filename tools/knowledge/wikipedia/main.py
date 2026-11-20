import html,json,re,sys,urllib.parse,urllib.request
def run(payload):
    query=payload["parameters"]["query"].strip()
    if not query or len(query)>250:return {"success":False,"error":"Recherche vide"}
    url="https://fr.wikipedia.org/w/api.php?"+urllib.parse.urlencode({
      "action":"query","list":"search","srsearch":query,"srlimit":3,"format":"json"})
    request=urllib.request.Request(url,headers={"User-Agent":"HestiaAssistant/0.3 (local assistant)"})
    with urllib.request.urlopen(request,timeout=12) as r:doc=json.load(r)
    items=[]
    for x in doc.get("query",{}).get("search",[]):
       title=x["title"]
       items.append({"title":title,
        "excerpt":html.unescape(re.sub("<[^>]+>","",x.get("snippet",""))),
        "url":"https://fr.wikipedia.org/wiki/"+urllib.parse.quote(title.replace(" ","_"))})
    return {"success":True,"query":query,"results":items}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

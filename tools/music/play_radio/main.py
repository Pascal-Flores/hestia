import json,os,socket,sys,urllib.parse,urllib.request
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Hestia/0.3"}),timeout=9) as r:return json.load(r)
def run(data):
    station=data["parameters"]["station"].strip()
    stations=get("https://de1.api.radio-browser.info/json/stations/search?"+urllib.parse.urlencode({
      "name":station,"limit":8,"hidebroken":"true","order":"votes","reverse":"true"}))
    valid=[x for x in stations if x.get("url_resolved","").startswith(("https://","http://"))]
    if not valid:return {"success":False,"error":"Station non trouvée","station":station}
    chosen=valid[0]
    addr=os.environ["MPD_HOST"]
    port=int(os.getenv("MPD_PORT","6600"))
    with socket.create_connection((addr,port),timeout=8) as sock:
      sock.settimeout(8);stream=sock.makefile("rwb",buffering=0)
      greeting=stream.readline().decode("utf-8","replace")
      if not greeting.startswith("OK MPD"):raise RuntimeError("Réponse MPD invalide")
      def command(s):
        if any(ch in s for ch in "\r\n"):raise ValueError("Commande MPD invalide")
        stream.write((s+"\n").encode())
        lines=[]
        while True:
          response=stream.readline().decode("utf-8","replace").strip()
          if response.startswith("ACK"):raise RuntimeError(response)
          if response=="OK":return lines
          if not response:raise RuntimeError("MPD déconnecté")
          lines.append(response)
      password=os.getenv("MPD_PASSWORD","")
      if password:command("password "+password)
      command("clear")
      url=chosen["url_resolved"].replace('"',"%22")
      command('add "'+url+'"')
      command("play")
    return {"success":True,"station":chosen.get("name"),"url":chosen["url_resolved"]}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

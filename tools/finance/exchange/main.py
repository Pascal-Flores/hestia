import json,sys,urllib.parse,urllib.request
def run(data):
    x=data["parameters"]
    src=x["from"].strip().upper()
    dst=x["to"].strip().upper()
    amount=float(x["amount"])
    if not (src.isalpha() and dst.isalpha() and len(src)==len(dst)==3 and abs(amount)<=1e9):
       return {"success":False,"error":"Devise ou montant invalide"}
    if src==dst:return {"success":True,"amount":amount,"from":src,"to":dst,"converted":amount,"rate":1}
    url="https://api.frankfurter.dev/v1/latest?"+urllib.parse.urlencode({
      "base":src,"symbols":dst})
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Hestia/0.3"}),timeout=12) as r:
      body=json.load(r)
    rate=body["rates"][dst]
    return {"success":True,"amount":amount,"from":src,"to":dst,
      "converted":round(amount*rate,4),"rate":rate,"date":body.get("date")}
try:print(json.dumps(run(json.load(sys.stdin)),ensure_ascii=False))
except Exception as e:print(json.dumps({"success":False,"error":str(e)},ensure_ascii=False))

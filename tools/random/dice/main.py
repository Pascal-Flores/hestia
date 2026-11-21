import json,secrets,sys
try:
    params=json.load(sys.stdin)["parameters"]
    count,sides=params["count"],params["sides"]
    if not (1<=count<=20 and 2<=sides<=100):raise ValueError("dés hors limites")
    results=[secrets.randbelow(sides)+1 for _ in range(count)]
    print(json.dumps({"success":True,"rolls":results,"total":sum(results)}))
except Exception as e:print(json.dumps({"success":False,"error":str(e)}))

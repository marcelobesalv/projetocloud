"""Roteiro completo da demonstração pelo ALB.  Uso: python3 ops/demo.py http://<alb>"""
import json, sys, time, urllib.error, urllib.request

URL = sys.argv[1].rstrip("/") + "/chat"
MSGS = [
    "Tenho um MacBook Air só com USB-C e Bluetooth, uso macOS. Procuro teclado e mouse sem fio.",
    "Esses são compatíveis com meu Mac? Prefiro layout ABNT2.",
    # Itens explícitos: com o LLM real, "o teclado, o mouse" é ambíguo e ele (corretamente) pede para escolher.
    "Monta um kit com o Logitech K380 ABNT2 grafite, o Pebble Mouse 2 grafite e o headset Zone Vibe 100 grafite.",
    "Consegue um desconto maior?",
    "Então adiciona o mousepad Speed XL preto.",
    "Fechado! Pode fazer o pedido no nome de Ana Souza.",
    "Sim, confirmo. Pode registrar.",
    "Qual o status do pedido?",
]
conv = None
for i, m in enumerate(MSGS, 1):
    if i == len(MSGS):
        time.sleep(5)  # dá tempo ao worker
    req = urllib.request.Request(URL, json.dumps({"conversation_id": conv, "message": m}).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=70))
    except urllib.error.HTTPError as e:
        print(i, "HTTP", e.code, e.read()[:300]); break
    conv = r["conversation_id"]
    print(f"\n### {i} ({time.time() - t:.1f}s, {r.get('status')}) > {m}\n{r['resposta'][:600]}")
print("\nconversa", conv)

"""Gerador de carga: N threads em GET / + 1 conversa com o LLM a cada 3 s; resumo a cada 30 s.
Uso: python3 ops/carga.py http://<alb> <segundos> [threads]   (~2,5 req/s por thread)"""
import collections, json, sys, threading, time, urllib.error, urllib.request

BASE = sys.argv[1].rstrip("/"); DUR = int(sys.argv[2]); THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 10
stats = collections.Counter(); lat = collections.defaultdict(list); lock = threading.Lock(); fim = time.time() + DUR

def rec(kind, code, dt):
    with lock:
        stats[(kind, code)] += 1; lat[kind].append(dt)

def chamar(kind, req, timeout):
    t = time.time()
    try:
        code = urllib.request.urlopen(req, timeout=timeout).status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = "erro"
    rec(kind, code, time.time() - t)

def get_loop():
    while time.time() < fim:
        chamar("GET /", BASE + "/", 10); time.sleep(0.4)

def chat_loop():
    corpo = json.dumps({"message": "Quais mouses sem fio voces tem para macOS?"}).encode()
    while time.time() < fim:
        chamar("POST /chat", urllib.request.Request(BASE + "/chat", corpo, {"Content-Type": "application/json"}), 70)
        time.sleep(3)

for alvo in [get_loop] * THREADS + [chat_loop]:
    threading.Thread(target=alvo, daemon=True).start()
ini = time.time()
while time.time() < fim + 5:
    time.sleep(30)
    with lock:
        s = " ".join(f"{k[0]}={k[1]}:{v}" for k, v in sorted(stats.items(), key=str))
        p90 = {k: sorted(v)[max(0, int(len(v) * 0.9) - 1)] for k, v in lat.items() if v}
    print(f"[{int(time.time() - ini)}s] {s} | p90 " + " ".join(f"{k}={v:.2f}s" for k, v in p90.items()), flush=True)

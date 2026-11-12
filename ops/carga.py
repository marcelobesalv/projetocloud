"""Gerador de carga em malha aberta (taxa fixa por degrau), com CSV para a análise quantitativa.

Uso:
  python3 ops/carga.py http://<alb> --degraus 0.5:300,1.5:600,3:600,5:600,0.5:900 --csv carga.csv
      cada degrau é "req/s:segundos" de GET / (página do chat, atrás do ALB)
  python3 ops/carga.py http://<alb> 600 6          (forma antiga: 6 "threads" ≈ 15 req/s por 600 s)

Malha aberta: as requisições saem no ritmo do degrau, mesmo que o servidor fique lento — a latência medida não
é mascarada por clientes esperando (como aconteceria com N threads em laço). Além do GET, uma conversa com o LLM
a cada --chat-cada segundos (0 desliga; no Bedrock cada turno custa tokens).

A política do chat-api é ALBRequestCountPerTarget = 30 req/min por task, ou seja 0,5 req/s por task: 1,5 req/s
pede 3 tasks, 3 req/s pede 6 (o máximo), e acima disso o cluster precisa de mais instâncias para as tasks.

Saída: uma linha a cada --janela segundos e, com --csv, uma linha por janela (horário UTC, degrau, alvo, enviados,
códigos, p50/p90/p99). No fim, um resumo por degrau. Junte com ops/metricas.py para o lado da AWS.
"""
import argparse
import collections
import csv
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("legado", nargs="*", type=float, help="forma antiga: <segundos> [threads]")
    ap.add_argument("--degraus", help='"req/s:segundos,..." (ex.: 0.5:300,3:600,0.5:600)')
    ap.add_argument("--csv", help="arquivo CSV com uma linha por janela")
    ap.add_argument("--janela", type=int, default=30, help="segundos por linha de resumo (padrão 30)")
    ap.add_argument("--chat-cada", type=float, default=3.0, help="segundos entre conversas com o LLM (0 desliga)")
    ap.add_argument("--max-concorrencia", type=int, default=200)
    a = ap.parse_args()
    if a.degraus:
        a.plano = [(float(r), int(s)) for r, s in (p.split(":") for p in a.degraus.split(",") if p.strip())]
    elif a.legado:
        dur = int(a.legado[0])
        threads = int(a.legado[1]) if len(a.legado) > 1 else 10
        a.plano = [(threads * 2.5, dur)]  # cada thread antiga fazia ~2,5 req/s
    else:
        ap.error("informe --degraus ou <segundos> [threads]")
    return a


class Coleta:
    """Contadores por janela e por degrau (thread-safe)."""

    def __init__(self):
        self.lock = threading.Lock()
        # janela atual, separada por degrau: uma janela que cruza a troca de degrau vira duas linhas
        self.janela = collections.defaultdict(collections.Counter)
        self.lat_janela = collections.defaultdict(lambda: collections.defaultdict(list))
        self.por_degrau = collections.defaultdict(collections.Counter)
        self.lat_degrau = collections.defaultdict(list)

    def registrar(self, degrau: int, tipo: str, codigo, dt: float):
        with self.lock:
            self.janela[degrau][(tipo, codigo)] += 1
            self.lat_janela[degrau][tipo].append(dt)
            self.por_degrau[degrau][(tipo, codigo)] += 1
            if tipo == "GET":
                self.lat_degrau[degrau].append(dt)

    def fechar_janela(self):
        """{degrau: (contagens, latências)} do que foi registrado desde a última chamada."""
        with self.lock:
            c, lat = self.janela, self.lat_janela
            self.janela = collections.defaultdict(collections.Counter)
            self.lat_janela = collections.defaultdict(lambda: collections.defaultdict(list))
        return {d: (c[d], lat[d]) for d in sorted(c)}


def pct(valores, p):
    if not valores:
        return None
    v = sorted(valores)
    return v[min(len(v) - 1, int(len(v) * p))]


def classe(codigo):
    if codigo == "erro":
        return "erro"
    return f"{str(codigo)[0]}xx"


def chamar(coleta, degrau, tipo, req, timeout):
    t = time.monotonic()
    try:
        codigo = urllib.request.urlopen(req, timeout=timeout).status
    except urllib.error.HTTPError as e:
        codigo = e.code
    except Exception:
        codigo = "erro"  # timeout, conexão recusada/resetada
    coleta.registrar(degrau, tipo, codigo, time.monotonic() - t)


def main():
    a = parse_args()
    base = a.url.rstrip("/")
    coleta = Coleta()
    pool = ThreadPoolExecutor(max_workers=a.max_concorrencia)
    estado = {"degrau": 0, "alvo": a.plano[0][0]}
    inicio_degrau = {}  # degrau -> horário UTC em que começou (o relatório usa para alinhar com o CloudWatch)
    parar = threading.Event()
    utc = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def gerador():
        for i, (rps, dur) in enumerate(a.plano):
            estado.update(degrau=i, alvo=rps)
            inicio_degrau[i] = utc()
            print(f"== {inicio_degrau[i]} degrau {i + 1}/{len(a.plano)}: {rps} req/s por {dur}s", flush=True)
            fim, prox = time.monotonic() + dur, time.monotonic()
            while time.monotonic() < fim and not parar.is_set():
                pool.submit(chamar, coleta, i, "GET", base + "/", 10)
                prox += 1 / rps
                espera = prox - time.monotonic()
                if espera > 0:
                    time.sleep(espera)
        parar.set()

    def chat():
        corpo = json.dumps({"message": "Quais mouses sem fio voces tem para macOS?"}).encode()
        while not parar.wait(a.chat_cada):
            req = urllib.request.Request(base + "/chat", corpo, {"Content-Type": "application/json"})
            pool.submit(chamar, coleta, estado["degrau"], "CHAT", req, 70)

    campos = ["utc", "t_s", "degrau", "degrau_inicio_utc", "alvo_rps", "get_total", "get_2xx", "get_4xx", "get_5xx",
              "get_erro", "get_p50_ms", "get_p90_ms", "get_p99_ms", "chat_total", "chat_ok", "chat_p90_ms"]
    arq = open(a.csv, "w", newline="", encoding="utf-8") if a.csv else None
    escritor = csv.DictWriter(arq, campos) if arq else None
    if escritor:
        escritor.writeheader()
    ini = time.time()
    ms = lambda v: None if v is None else round(v * 1000)  # noqa: E731

    def emitir():
        agora, t_s = utc(), int(time.time() - ini)
        for d, (c, lat) in coleta.fechar_janela().items():
            get = collections.Counter()
            for (tipo, cod), n in c.items():
                if tipo == "GET":
                    get[classe(cod)] += n
            chat_total = sum(n for (t, _), n in c.items() if t == "CHAT")
            chat_ok = sum(n for (t, cod), n in c.items() if t == "CHAT" and cod == 200)
            linha = {
                "utc": agora, "t_s": t_s, "degrau": d + 1, "degrau_inicio_utc": inicio_degrau.get(d, ""),
                "alvo_rps": a.plano[d][0], "get_total": sum(get.values()), "get_2xx": get["2xx"],
                "get_4xx": get["4xx"], "get_5xx": get["5xx"], "get_erro": get["erro"],
                "get_p50_ms": ms(pct(lat["GET"], 0.5)), "get_p90_ms": ms(pct(lat["GET"], 0.9)),
                "get_p99_ms": ms(pct(lat["GET"], 0.99)), "chat_total": chat_total, "chat_ok": chat_ok,
                "chat_p90_ms": ms(pct(lat["CHAT"], 0.9)),
            }
            if escritor:
                escritor.writerow(linha)
                arq.flush()
            print(f"[{t_s}s d{d + 1} {linha['alvo_rps']}rps] GET {linha['get_total']} "
                  f"(2xx {linha['get_2xx']}, 5xx {linha['get_5xx']}, erro {linha['get_erro']}) "
                  f"p50 {linha['get_p50_ms']}ms p90 {linha['get_p90_ms']}ms | chat {chat_ok}/{chat_total} "
                  f"p90 {linha['chat_p90_ms']}ms", flush=True)

    threading.Thread(target=gerador, daemon=True).start()
    if a.chat_cada > 0:
        threading.Thread(target=chat, daemon=True).start()
    try:
        while not parar.wait(a.janela):
            emitir()
    except KeyboardInterrupt:
        parar.set()
    print("== fim da carga; esperando as requisições em andamento", flush=True)
    pool.shutdown(wait=True)  # em andamento terminam (no máximo o timeout de cada uma)
    emitir()
    if arq:
        arq.close()

    print("\n== resumo por degrau")
    print("degrau  alvo_rps  GET  2xx%   5xx  erro  p50_ms  p90_ms  p99_ms")
    for i, (rps, _) in enumerate(a.plano):
        c = coleta.por_degrau[i]
        total = sum(n for (t, _), n in c.items() if t == "GET")
        cls = collections.Counter()
        for (t, cod), n in c.items():
            if t == "GET":
                cls[classe(cod)] += n
        lat = coleta.lat_degrau[i]
        f = lambda p: "-" if not lat else f"{pct(lat, p) * 1000:.0f}"  # noqa: E731
        ok = f"{100 * cls['2xx'] / total:.2f}" if total else "-"
        print(f"{i + 1:>6}  {rps:>8}  {total:>4}  {ok:>5}  {cls['5xx']:>4}  {cls['erro']:>4}  {f(0.5):>6}  "
              f"{f(0.9):>6}  {f(0.99):>6}")


if __name__ == "__main__":
    main()

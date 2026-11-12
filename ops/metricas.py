"""Relatório quantitativo de um teste de carga a partir do CloudWatch (só AWS CLI, sem boto3).

Uso (no CloudShell da região, depois da carga):
  python3 ops/metricas.py --regiao sa-east-1 --carga carga.csv --saida relatorio
  python3 ops/metricas.py --regiao sa-east-1 --inicio 2026-11-20T14:00:00Z --fim 2026-11-20T15:00:00Z --saida relatorio

Com --carga, a janela e os degraus vêm do CSV do ops/carga.py (com 5 min de folga depois, para ver o scale-in
começar). Gera:
  relatorio.csv   uma linha por minuto: tasks, instâncias, requisições, 5xx, latência, CPU, reserva do cluster, fila
  relatorio.md    resumo por degrau, tempos de reação do auto scaling e linha do tempo das atividades de escala
  relatorio.html  gráficos (Chart.js) para a apresentação

As métricas do CloudWatch chegam com 1–3 min de atraso: rode alguns minutos depois de a carga terminar.
"""
import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

P = "setup-pronto"
CLUSTER = f"{P}-cluster"
ASG = f"{P}-ecs-asg"
SERVICOS = ["chat-api", "commerce-api", "order-worker"]


def aws(*args):
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"aws {' '.join(args[:2])} falhou: {r.stderr.strip()}")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    """Timestamp da AWS CLI ("...Z" ou "...+00:00") em UTC; o fromisoformat do Python 3.9 não aceita "Z"."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def consultas(lb, tg):
    """(id, rótulo, namespace, métrica, dimensões, estatística)"""
    d_lb = {"LoadBalancer": lb}
    q = [
        ("inst", "instâncias em serviço", "AWS/AutoScaling", "GroupInServiceInstances", {"AutoScalingGroupName": ASG}, "Maximum"),
        ("inst_desej", "instâncias desejadas", "AWS/AutoScaling", "GroupDesiredCapacity", {"AutoScalingGroupName": ASG}, "Maximum"),
        ("req", "requisições no ALB", "AWS/ApplicationELB", "RequestCount", d_lb, "Sum"),
        ("req_task", "requisições por task do chat", "AWS/ApplicationELB", "RequestCountPerTarget", {"TargetGroup": tg, "LoadBalancer": lb}, "Sum"),
        ("e5xx_alvo", "5xx das tasks", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", d_lb, "Sum"),
        ("e5xx_alb", "5xx do ALB (502/503/504)", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", d_lb, "Sum"),
        ("lat_p50", "latência p50 (s)", "AWS/ApplicationELB", "TargetResponseTime", d_lb, "p50"),
        ("lat_p90", "latência p90 (s)", "AWS/ApplicationELB", "TargetResponseTime", d_lb, "p90"),
        ("cpu_res", "CPU reservada do cluster (%)", "AWS/ECS", "CPUReservation", {"ClusterName": CLUSTER}, "Average"),
        ("mem_res", "memória reservada do cluster (%)", "AWS/ECS", "MemoryReservation", {"ClusterName": CLUSTER}, "Average"),
        ("cp_res", "CapacityProviderReservation (%)", "AWS/ECS/ManagedScaling", "CapacityProviderReservation",
         {"CapacityProviderName": f"{P}-cp", "ClusterName": CLUSTER}, "Average"),
        ("fila", "mensagens na fila", "AWS/SQS", "ApproximateNumberOfMessagesVisible", {"QueueName": f"{P}-orders"}, "Maximum"),
        ("dlq", "mensagens na DLQ", "AWS/SQS", "ApproximateNumberOfMessagesVisible", {"QueueName": f"{P}-orders-dlq"}, "Maximum"),
        ("llm_p90", "latência do LLM p90 (ms)", "SetupPronto", "LLMLatency", {}, "p90"),
    ]
    for s in SERVICOS:
        k = s.split("-")[0]
        q.append((f"tasks_{k}", f"tasks {s}", "ECS/ContainerInsights", "RunningTaskCount",
                  {"ClusterName": CLUSTER, "ServiceName": s}, "Maximum"))
        q.append((f"cpu_{k}", f"CPU {s} (%)", "AWS/ECS", "CPUUtilization", {"ClusterName": CLUSTER, "ServiceName": s}, "Average"))
    return q


def buscar(qs, inicio, fim):
    mdq = [{"Id": i, "Label": i, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": ns, "MetricName": m, "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
        "Period": 60, "Stat": st}} for i, _, ns, m, dims, st in qs]
    serie = defaultdict(dict)  # minuto -> id -> valor
    token = None
    while True:
        args = ["cloudwatch", "get-metric-data", "--start-time", iso(inicio), "--end-time", iso(fim),
                "--scan-by", "TimestampAscending", "--metric-data-queries", json.dumps(mdq)]
        if token:
            args += ["--next-token", token]
        r = aws(*args)
        for res in r.get("MetricDataResults", []):
            for ts, v in zip(res["Timestamps"], res["Values"]):
                minuto = parse_ts(ts)
                serie[minuto][res["Id"]] = v
        token = r.get("NextToken")
        if not token:
            return serie


def atividades(inicio, fim):
    evs = []
    r = aws("application-autoscaling", "describe-scaling-activities", "--service-namespace", "ecs")
    for a in r.get("ScalingActivities", []):
        t = parse_ts(a["StartTime"])
        if inicio <= t <= fim and f"/{CLUSTER}/" in a["ResourceId"]:
            evs.append((t, a["ResourceId"].split("/")[-1], a["StatusCode"], a["Description"]))
    r = aws("autoscaling", "describe-scaling-activities", "--auto-scaling-group-name", ASG, "--max-items", "100")
    for a in r.get("Activities", []):
        t = parse_ts(a["StartTime"])
        if inicio <= t <= fim:
            evs.append((t, "ASG", a["StatusCode"], a["Description"]))
    return sorted(evs)


def ler_carga(caminho):
    """Degraus do CSV do carga.py: [(degrau, alvo_rps, início, fim)] e os totais do cliente por degrau."""
    with open(caminho, newline="", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    if not linhas:
        sys.exit(f"{caminho} está vazio")
    inicio, alvo, cliente = {}, {}, defaultdict(lambda: defaultdict(int))
    for ln in linhas:
        d = int(ln["degrau"])
        inicio.setdefault(d, parse_utc(ln["degrau_inicio_utc"]))
        alvo[d] = float(ln["alvo_rps"])
        for k in ("get_total", "get_2xx", "get_5xx", "get_erro"):
            cliente[d][k] += int(ln[k])
    ordem = sorted(inicio)
    fim_carga = max(parse_utc(ln["utc"]) for ln in linhas)
    degraus = [(d, alvo[d], inicio[d], inicio[ordem[n + 1]] if n + 1 < len(ordem) else fim_carga)
               for n, d in enumerate(ordem)]
    return degraus, cliente


def fmt(v, casas=0):
    if v is None:
        return "-"
    return f"{v:.{casas}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regiao", required=True)
    ap.add_argument("--carga", help="CSV gerado pelo ops/carga.py --csv")
    ap.add_argument("--inicio")
    ap.add_argument("--fim")
    ap.add_argument("--folga-min", type=int, default=5, help="minutos depois da carga (padrão 5)")
    ap.add_argument("--saida", default="relatorio")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console do Windows

    import os
    os.environ["AWS_REGION"] = os.environ["AWS_DEFAULT_REGION"] = a.regiao

    degraus, cliente = ([], {})
    if a.carga:
        degraus, cliente = ler_carga(a.carga)
        inicio = degraus[0][2] - timedelta(minutes=2)
        fim = degraus[-1][3] + timedelta(minutes=a.folga_min)
    elif a.inicio and a.fim:
        inicio, fim = parse_utc(a.inicio), parse_utc(a.fim)
    else:
        sys.exit("informe --carga ou --inicio/--fim")
    inicio = inicio.replace(second=0)

    lb_arn = aws("elbv2", "describe-load-balancers", "--names", f"{P}-alb")["LoadBalancers"][0]["LoadBalancerArn"]
    tg_arn = aws("elbv2", "describe-target-groups", "--names", f"{P}-chat-tg")["TargetGroups"][0]["TargetGroupArn"]
    lb = lb_arn.split(":loadbalancer/")[1]
    tg = tg_arn.split(":")[-1]
    qs = consultas(lb, tg)
    rotulo = {i: r for i, r, *_ in qs}
    serie = buscar(qs, inicio, fim)
    evs = atividades(inicio, fim)
    minutos = sorted(serie)
    if not minutos:
        sys.exit("o CloudWatch não devolveu pontos para a janela (espere uns minutos e rode de novo)")

    def degrau_do(m):
        for d, rps, ini, fim_d in degraus:
            if ini <= m + timedelta(seconds=30) <= fim_d:
                return d, rps
        return None, None

    # --- CSV por minuto
    ids = [i for i, *_ in qs]
    with open(f"{a.saida}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["utc", "degrau", "alvo_req_min"] + ids)
        for m in minutos:
            d, rps = degrau_do(m)
            w.writerow([iso(m), d or "", round(rps * 60) if rps else ""] + [serie[m].get(i, "") for i in ids])

    # --- resumo
    def valores(i, ini=None, fim_=None):
        return [serie[m][i] for m in minutos if i in serie[m] and (ini is None or ini <= m < fim_)]

    def maxi(i, *j):
        v = valores(i, *j)
        return max(v) if v else None

    def media(i, *j):
        v = valores(i, *j)
        return sum(v) / len(v) if v else None

    req_total = sum(valores("req"))
    e5xx = sum(valores("e5xx_alvo")) + sum(valores("e5xx_alb"))
    md = [f"# Teste de carga — {a.regiao}", "",
          f"Janela: {iso(inicio)} a {iso(fim)} (UTC), {len(minutos)} minutos de métricas.", "",
          "## Totais (lado do ALB)", "",
          f"- Requisições: **{req_total:.0f}**; respostas 5xx: **{e5xx:.0f}** "
          f"(disponibilidade {100 * (1 - e5xx / req_total) if req_total else 100:.3f}%)",
          f"- Tasks do chat-api: {fmt(min(valores('tasks_chat') or [0]))} → {fmt(maxi('tasks_chat'))}; "
          f"instâncias: {fmt(min(valores('inst') or [0]))} → {fmt(maxi('inst'))}",
          f"- Latência p90 do ALB: mediana dos minutos {fmt(sorted(valores('lat_p90'))[len(valores('lat_p90')) // 2] * 1000 if valores('lat_p90') else None)} ms, "
          f"pior minuto {fmt((maxi('lat_p90') or 0) * 1000)} ms",
          f"- DLQ no fim: {fmt((valores('dlq') or [None])[-1])}", ""]

    if degraus:
        md += ["## Por degrau", "",
               "| degrau | alvo (req/s) | duração | req/min no ALB | req/min por task | tasks chat (máx) | instâncias (máx) | CPU reservada (máx) | 5xx ALB | p90 ALB (ms) | cliente: 2xx % | cliente: erros |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for d, rps, ini, fim_d in degraus:
            j = (ini.replace(second=0), fim_d)
            e = sum(valores("e5xx_alvo", *j)) + sum(valores("e5xx_alb", *j))
            c = cliente[d]
            ok = 100 * c["get_2xx"] / c["get_total"] if c["get_total"] else None
            md.append(f"| {d} | {rps} | {int((fim_d - ini).total_seconds() // 60)} min | {fmt(media('req', *j))} | "
                      f"{fmt(media('req_task', *j))} | {fmt(maxi('tasks_chat', *j))} | {fmt(maxi('inst', *j))} | "
                      f"{fmt(maxi('cpu_res', *j))}% | {e:.0f} | {fmt((media('lat_p90', *j) or 0) * 1000)} | "
                      f"{fmt(ok, 2)} | {c['get_5xx'] + c['get_erro']} |")
        md += ["", "### Tempo de reação", "",
               "Do início de cada degrau até o primeiro minuto em que o número de tasks do chat-api / de instâncias "
               "chegou ao valor máximo daquele degrau (no degrau de descida, ao mínimo). Resolução de 1 min. O último "
               "degrau inclui a folga depois da carga, onde acontece o scale-in.", "",
               "| degrau | tasks: de → para | tempo | instâncias: de → para | tempo |", "|---|---|---|---|---|"]
        for n, (d, rps, ini, fim_d) in enumerate(degraus):
            linha = [str(d)]
            ate = fim if n == len(degraus) - 1 else fim_d
            for i in ("tasks_chat", "inst"):
                ms = [m for m in minutos if ini.replace(second=0) <= m < ate and i in serie[m]]
                if not ms:
                    linha += ["-", "-"]
                    continue
                antes, alvo = serie[ms[0]][i], max(serie[m][i] for m in ms)
                if alvo <= antes:
                    alvo = min(serie[m][i] for m in ms)  # degrau de descida: tempo até o mínimo
                chegou = next(m for m in ms if serie[m][i] == alvo)
                linha += [f"{antes:.0f} → {alvo:.0f}", "-" if alvo == antes else f"{(chegou - ini).total_seconds() / 60:.0f} min"]
            md.append("| " + " | ".join(linha) + " |")

    md += ["", "## Atividades de escala", ""]
    md += [f"- `{t:%H:%M:%S}` **{alvo}** ({st}): {desc}" for t, alvo, st, desc in evs] or ["(nenhuma na janela)"]
    md += ["", "Séries por minuto: `" + a.saida + ".csv`. Gráficos: `" + a.saida + ".html`.", ""]
    with open(f"{a.saida}.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # --- HTML com gráficos
    rot = [f"{m:%H:%M}" for m in minutos]
    serie_js = lambda i, esc=1: [None if i not in serie[m] else round(serie[m][i] * esc, 2) for m in minutos]  # noqa: E731
    alvo_min = [round(degrau_do(m)[1] * 60) if degrau_do(m)[1] else None for m in minutos]
    graficos = [
        ("Carga e erros (por minuto)", [("requisições no ALB", serie_js("req")), ("alvo da carga (req/min)", alvo_min),
                                        ("5xx das tasks", serie_js("e5xx_alvo")), ("5xx do ALB", serie_js("e5xx_alb"))]),
        ("Elasticidade: tasks e instâncias", [("tasks chat-api", serie_js("tasks_chat")), ("tasks commerce-api", serie_js("tasks_commerce")),
                                               ("tasks order-worker", serie_js("tasks_order")), ("instâncias em serviço", serie_js("inst")),
                                               ("instâncias desejadas", serie_js("inst_desej"))]),
        ("Requisições por task do chat (alvo da política: 30/min)", [("req/min por task", serie_js("req_task"))]),
        ("Latência do ALB (ms)", [("p50", serie_js("lat_p50", 1000)), ("p90", serie_js("lat_p90", 1000))]),
        ("Reserva do cluster (%)", [("CPU reservada", serie_js("cpu_res")), ("memória reservada", serie_js("mem_res")),
                                    ("CapacityProviderReservation", serie_js("cp_res"))]),
    ]
    blocos = "\n".join(f'<section><h2>{t}</h2><canvas id="g{n}"></canvas></section>' for n, (t, _) in enumerate(graficos))
    dados = json.dumps([{"t": t, "s": [{"label": l, "data": d} for l, d in s]} for t, s in graficos])
    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teste de carga {a.regiao}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1d2433;background:#fff}}
h1{{font-size:22px}}h2{{font-size:16px;margin:28px 0 8px}}p{{color:#555}}</style></head><body>
<h1>Teste de carga — {a.regiao}</h1><p>{iso(inicio)} a {iso(fim)} UTC · {req_total:.0f} requisições · {e5xx:.0f} respostas 5xx ·
tasks do chat {fmt(min(valores('tasks_chat') or [0]))}→{fmt(maxi('tasks_chat'))} · instâncias {fmt(min(valores('inst') or [0]))}→{fmt(maxi('inst'))}</p>
{blocos}
<script>const rot={json.dumps(rot)};const G={dados};
G.forEach((g,n)=>new Chart(document.getElementById('g'+n),{{type:'line',data:{{labels:rot,datasets:g.s.map(s=>({{label:s.label,data:s.data,stepped:true,spanGaps:true,pointRadius:0,borderWidth:2}}))}},
options:{{animation:false,interaction:{{mode:'index',intersect:false}},scales:{{y:{{beginAtZero:true}}}}}}}}));</script></body></html>"""
    with open(f"{a.saida}.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("\n".join(md))
    print(f"\narquivos: {a.saida}.csv {a.saida}.md {a.saida}.html")


if __name__ == "__main__":
    main()

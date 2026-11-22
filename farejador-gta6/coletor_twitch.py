#!/usr/bin/env python3
"""
Coletor Twitch -> JSON de candidatos para o Bot 1 (Farejador de Cortes GTA 6).

Busca os clipes de GTA 6 criados nas ultimas N horas, ordenados por views,
e gera o JSON que o Bot 1 espera como entrada. Nao usa LLM: so coleta.

Credenciais: crie um arquivo .env ao lado deste script com duas linhas:

    TWITCH_CLIENT_ID=xxxxxxxx
    TWITCH_CLIENT_SECRET=xxxxxxxx

(variaveis de ambiente tambem funcionam e tem prioridade sobre o .env)

Uso:
    python coletor_twitch.py                      # ultimas 24h
    python coletor_twitch.py --horas 12
    python coletor_twitch.py --min-views 500 --saida entrada/candidatos.json

Registrar o app: https://dev.twitch.tv/console/apps -> Register Your Application
(Category: Application Integration / Client Type: Confidential)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HELIX = "https://api.twitch.tv/helix"
OAUTH = "https://id.twitch.tv/oauth2/token"
JOGO = "Grand Theft Auto VI"
FUSO = timezone(timedelta(hours=-3))  # America/Sao_Paulo

# Idiomas tratados como "BR". O resto cai em internacional.
IDIOMAS_BR = {"pt"}

# Teto da janela final, em segundos. O padrao da casa e 45-120s.
TETO_JANELA = 120


def carregar_env():
    """Le um .env ao lado do script. Variavel de ambiente real tem prioridade."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8-sig") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if chave and chave not in os.environ:
                os.environ[chave] = valor


def _get(url, headers, tentativas=3):
    for n in range(tentativas):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            corpo = e.read().decode(errors="replace")[:300]
            if e.code == 429 and n < tentativas - 1:
                continue
            raise SystemExit(f"ERRO HTTP {e.code} em {url}\n{corpo}")
        except URLError as e:
            if n == tentativas - 1:
                raise SystemExit(f"ERRO de rede: {e.reason}")
    return None


def autenticar(client_id, client_secret):
    """App access token via client_credentials. Nao precisa de login de usuario."""
    dados = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()
    req = Request(OAUTH, data=dados, method="POST")
    try:
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["access_token"]
    except HTTPError as e:
        raise SystemExit(
            "Falha na autenticacao. Confira TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET.\n"
            + e.read().decode(errors="replace")[:300]
        )


def achar_game_id(headers, nome=JOGO):
    r = _get(f"{HELIX}/games?{urlencode({'name': nome})}", headers)
    if not r.get("data"):
        raise SystemExit(f"Jogo '{nome}' nao encontrado na Twitch.")
    j = r["data"][0]
    print(f"[i] Jogo: {j['name']} (id {j['id']})", file=sys.stderr)
    return j["id"]


def buscar_clipes(headers, game_id, horas, teto_paginas=10):
    """Helix devolve ordenado por view_count desc. Pagina ate o teto."""
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(hours=horas)
    cursor, clipes = None, []

    for _ in range(teto_paginas):
        p = {
            "game_id": game_id,
            "started_at": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "first": 100,
        }
        if cursor:
            p["after"] = cursor
        r = _get(f"{HELIX}/clips?{urlencode(p)}", headers)
        lote = r.get("data", [])
        clipes.extend(lote)
        cursor = r.get("pagination", {}).get("cursor")
        if not cursor or not lote:
            break

    print(f"[i] {len(clipes)} clipes brutos nas ultimas {horas}h", file=sys.stderr)
    return clipes


def duracao_para_segundos(texto):
    """Twitch devolve duracao de VOD como '26h31m22s'. Converte para segundos."""
    import re
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", (texto or "").strip())
    if not m:
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def buscar_vods(headers, video_ids):
    """Duracao de cada VOD. Serve para avisar sobre o caso de >26h, onde o
    relogio interno do MPEG-TS da a volta e o --download-sections trava."""
    duracoes = {}
    ids = [i for i in dict.fromkeys(video_ids) if i]
    for n in range(0, len(ids), 100):
        lote = ids[n : n + 100]
        q = "&".join(f"id={i}" for i in lote)
        try:
            r = _get(f"{HELIX}/videos?{q}", headers)
        except SystemExit:
            continue  # sem duracao de VOD o coletor ainda funciona
        for v in r.get("data", []):
            duracoes[v["id"]] = duracao_para_segundos(v.get("duration"))
    return duracoes


def agrupar_momentos(cands, tolerancia=15, margem_antes=12, margem_depois=8):
    """Varias pessoas clipam o mesmo momento. Isso e duas coisas ao mesmo tempo:
    duplicata (ocupa vagas) e SINAL DE PICO (o chat votou com o botao de clipe).

    Agrupa clipes do mesmo streamer no mesmo VOD cujas faixas [inicio, fim] se
    tocam (ou ficam a menos de `tolerancia` segundos). Mantem um representante
    por momento e registra quantos clipes existiam. A janela do representante
    passa a cobrir a UNIAO do grupo, que e mais fiel ao momento inteiro do que
    qualquer clipe sozinho.

    Clipe sem vod_offset nao da para agrupar: passa intacto.
    """
    agrupaveis, intactos = [], []
    for c in cands:
        if c.get("clipe_inicio_vod_s") is not None and c.get("video_id"):
            agrupaveis.append(c)
        else:
            c["clipes_do_mesmo_momento"] = 1
            c["clipadores"] = [c.get("clipador")] if c.get("clipador") else []
            c["clipadores_distintos"] = 1 if c.get("clipador") else 0
            c["views_do_momento"] = c.get("views", 0)
            intactos.append(c)

    grupos = {}
    for c in agrupaveis:
        grupos.setdefault((c["criador"], c["video_id"]), []).append(c)

    saida = []
    for faixa in grupos.values():
        faixa.sort(key=lambda x: x["clipe_inicio_vod_s"])
        cluster = [faixa[0]]
        fim_cluster = faixa[0]["vod_offset_s"]

        def fechar(grupo):
            # representante: o mais visto, que e o que o publico mais validou
            rep = max(grupo, key=lambda x: x.get("views", 0))
            # O sinal de pico sao PESSOAS distintas, nao clipes: um unico
            # clipador apertando o botao 4 vezes nao e o publico votando.
            # Visto em campo: caseoh "kyle 1" tinha 4 clipes, todos do mesmo
            # clipador. Por isso clipadores_distintos e o campo que vale.
            nomes = [g["clipador"] for g in grupo if g.get("clipador")]
            rep["clipes_do_mesmo_momento"] = len(grupo)
            rep["clipadores"] = sorted(set(nomes))[:6]
            rep["clipadores_distintos"] = len(set(nomes))
            rep["views_do_momento"] = sum(g.get("views", 0) for g in grupo)
            if len(grupo) > 1:
                rep["links_duplicados"] = [g["link"] for g in grupo if g is not rep]
                # Janela cobrindo a uniao do grupo -- mas so se couber no padrao
                # da casa. Grupo espalhado gera uniao gigante (ja vimos 263s);
                # nesse caso a uniao nao e "um momento", sao varios proximos.
                # Cai para a faixa do proprio representante.
                ini = min(g["clipe_inicio_vod_s"] for g in grupo)
                fim = max(g["vod_offset_s"] for g in grupo)
                if (fim - ini) + margem_antes + margem_depois > TETO_JANELA:
                    ini, fim = rep["clipe_inicio_vod_s"], rep["vod_offset_s"]
                    rep["uniao_descartada_s"] = (
                        max(g["vod_offset_s"] for g in grupo)
                        - min(g["clipe_inicio_vod_s"] for g in grupo)
                    )
                rep["vod_entrada_s"] = max(ini - margem_antes, 0)
                rep["vod_saida_s"] = fim + margem_depois
                rep["precisa_contexto"] = (fim - ini) < 45
                rep["contexto_recuperavel"] = True
            rep["janela_total_s"] = (
                rep["vod_saida_s"] - rep["vod_entrada_s"]
                if rep.get("vod_entrada_s") is not None else None
            )
            saida.append(rep)

        for c in faixa[1:]:
            if c["clipe_inicio_vod_s"] <= fim_cluster + tolerancia:
                cluster.append(c)
                fim_cluster = max(fim_cluster, c["vod_offset_s"])
            else:
                fechar(cluster)
                cluster, fim_cluster = [c], c["vod_offset_s"]
        fechar(cluster)

    return saida + intactos


def normalizar(c, agora, vod_duracoes=None):
    criado = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    horas = max((agora - criado).total_seconds() / 3600, 0.25)
    idioma = (c.get("language") or "").split("-")[0]

    dur = round(float(c.get("duration", 0)), 1)

    vod = None
    offset = c.get("vod_offset")
    if c.get("video_id") and offset is not None:
        vod = f"https://www.twitch.tv/videos/{c['video_id']}?t={int(offset)}s"

    # Clipe curto demais para o padrao da casa (45-120s), mas o momento pode
    # prestar: se existe VOD, da para puxar contexto antes e depois.
    #
    # ATENCAO: vod_offset marca o FIM do clipe, nao o comeco. A documentacao da
    # Twitch diz o contrario e esta errada. Verificado em 5 clipes de duracoes
    # diferentes (9,7s a 54,8s): o inicio real no VOD sempre caiu em
    # offset - duracao, com erro abaixo de 1s. Entao:
    #     clipe comeca em  offset - dur
    #     clipe termina em offset
    # 12s, nao 30: com 30 o momento caia no segundo 45 de um video de 70,
    # e dois tercos eram o streamer andando. Lead-in e preparacao, nao enchimento.
    MARGEM_ANTES, MARGEM_DEPOIS, ALVO_MIN = 12, 8, 45
    curto = dur < ALVO_MIN
    vod_entrada = vod_saida = None
    if curto and offset is not None:
        vod_entrada = max(int(offset) - int(dur) - MARGEM_ANTES, 0)
        vod_saida = int(offset) + MARGEM_DEPOIS

    # Depois de ~26,5h DENTRO do VOD o relogio do MPEG-TS da a volta e o ffmpeg
    # trava (nao da erro) ao tentar pular para o ponto. O que decide e a POSICAO
    # do trecho, nao a duracao do VOD: um clipe na hora 2 de uma live de 30h
    # baixa normalmente. Por isso a conta usa o ponto final que vamos buscar.
    LIMITE_WRAP = 26.5 * 3600
    vod_dur = (vod_duracoes or {}).get(c.get("video_id"))
    posicao = vod_saida if vod_saida is not None else offset
    vod_longo = bool(posicao is not None and posicao > LIMITE_WRAP)

    return {
        "plataforma": "twitch",
        "jogo": JOGO,
        "link": c["url"],
        "titulo_original": c.get("title", ""),
        "criador": c.get("broadcaster_name", ""),
        "clipador": c.get("creator_name", ""),
        "idioma": idioma,
        "origem": "br" if idioma in IDIOMAS_BR else "internacional",
        "precisa_legenda_ptbr": idioma not in IDIOMAS_BR,
        "views": c.get("view_count", 0),
        "publicado_em": c["created_at"],
        "horas_desde_publicacao": round(horas, 2),
        "views_por_hora": round(c.get("view_count", 0) / horas, 1),
        "duracao_s": dur,
        "clipe_pronto": True,
        "fonte_do_timestamp": "clipe_twitch",
        "entrada": "00:00",
        "saida": None,  # preenchido abaixo
        "precisa_contexto": curto,
        "contexto_recuperavel": curto and vod_entrada is not None,
        "vod_url": vod,
        "video_id": c.get("video_id") or None,
        "vod_offset_s": offset,
        "clipe_inicio_vod_s": (int(offset) - int(dur)) if offset is not None else None,
        "vod_entrada_s": vod_entrada,
        "vod_saida_s": vod_saida,
        "vod_duracao_s": vod_dur,
        "vod_longo": vod_longo,
        "destaque_twitch": c.get("is_featured", False),
        "thumbnail": c.get("thumbnail_url", ""),
        "permissao": "desconhecido",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horas", type=int, default=24)
    ap.add_argument("--min-views", type=int, default=200,
                    help="piso de views para clipes internacionais")
    ap.add_argument("--min-views-br", type=int, default=50,
                    help="piso separado para BR: a audiencia e menor, entao 200 "
                         "views num clipe br ja e traco alto e o filtro estrangula")
    ap.add_argument("--min-dur", type=float, default=8.0)
    ap.add_argument("--max-dur", type=float, default=60.0,
                    help="clipe da Twitch nao passa de 60s; acima disso so via VOD")
    ap.add_argument("--teto", type=int, default=60,
                    help="quantos candidatos mandar pro Bot 1 (por origem)")
    ap.add_argument("--saida", default="candidatos.json")
    a = ap.parse_args()

    carregar_env()
    cid = os.environ.get("TWITCH_CLIENT_ID")
    sec = os.environ.get("TWITCH_CLIENT_SECRET")
    if not cid or not sec:
        raise SystemExit(
            "Faltam as credenciais da Twitch.\n"
            "Crie um arquivo .env ao lado deste script com:\n"
            "  TWITCH_CLIENT_ID=xxxx\n"
            "  TWITCH_CLIENT_SECRET=xxxx\n"
            "Pegue em https://dev.twitch.tv/console/apps (Client Type: Confidential)."
        )

    headers = {"Client-Id": cid, "Authorization": f"Bearer {autenticar(cid, sec)}"}
    agora = datetime.now(timezone.utc)

    brutos = buscar_clipes(headers, achar_game_id(headers), a.horas)
    vod_duracoes = buscar_vods(headers, [c.get("video_id") for c in brutos])

    cands = []
    for c in brutos:
        d = c.get("duration", 0)
        if not (a.min_dur <= float(d) <= a.max_dur):
            continue
        idioma = (c.get("language") or "").split("-")[0]
        piso = a.min_views_br if idioma in IDIOMAS_BR else a.min_views
        if c.get("view_count", 0) < piso:
            continue
        n = normalizar(c, agora, vod_duracoes)
        m, s = divmod(int(round(n["duracao_s"])), 60)
        n["saida"] = f"{m:02d}:{s:02d}"
        cands.append(n)

    # Agrupa ANTES do teto, senao duplicata ocupa vaga de candidato real.
    antes_do_agrupamento = len(cands)
    cands = agrupar_momentos(cands)
    duplicatas = antes_do_agrupamento - len(cands)

    # Ordena por tracao, nao por views absolutas.
    cands.sort(key=lambda x: x["views_por_hora"], reverse=True)
    br = [c for c in cands if c["origem"] == "br"][: a.teto]
    intl = [c for c in cands if c["origem"] == "internacional"][: a.teto]

    # Tracao relativa: percentil DENTRO do proprio grupo de origem.
    # Comparar views/hora entre BR e internacional e invalido -- o clipe gringo
    # tem mais views porque o streamer tem mais publico na Twitch, nao porque o
    # momento e melhor. Sem isso o agente escolhe internacional sempre.
    for grupo in (br, intl):
        n = len(grupo)
        for i, c in enumerate(grupo):  # grupo ja esta ordenado desc
            c["tracao_relativa"] = round(100 * (n - i) / n) if n else 0

    saida = {
        "coletado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
        "fonte": "twitch_helix",
        "janela_horas": a.horas,
        "filtros": {
            "min_views_internacional": a.min_views,
            "min_views_br": a.min_views_br,
            "duracao_s": [a.min_dur, a.max_dur],
            "teto_por_origem": a.teto,
        },
        "total_bruto": len(brutos),
        "total_filtrado": len(cands),
        "duplicatas_agrupadas": duplicatas,
        "momentos_multiclipe": sum(
            1 for c in br + intl if c.get("clipes_do_mesmo_momento", 1) > 1
        ),
        "descartados_por_teto": max(len(cands) - len(br) - len(intl), 0),
        "precisam_contexto": sum(1 for c in br + intl if c["precisa_contexto"]),
        "contexto_perdido": sum(
            1 for c in br + intl
            if c["precisa_contexto"] and not c["contexto_recuperavel"]
        ),
        "candidatos_br": br,
        "candidatos_internacional": intl,
        "nota_para_o_agente": (
            "Todo clipe aqui ja vem cortado: entrada/saida sao do proprio clipe. "
            "vod_url aponta o segundo exato na live original, quando existe. "
            "precisa_contexto=true significa clipe abaixo de 45s: NAO penalize por "
            "isso. Se contexto_recuperavel=true, o corte final sai do VOD usando "
            "vod_entrada_s/vod_saida_s, que ja incluem margem antes e depois. "
            "Se precisa_contexto=true e contexto_recuperavel=false, o momento so "
            "existe no clipe cru: ai sim penalize, porque nao da para esticar. "
            "JOGO: todo candidato deste arquivo e de Grand Theft Auto VI -- a coleta busca pelo game_id do jogo na Twitch, entao nao existe clipe de outro jogo aqui. Pode escrever GTA 6 no titulo sem precisar de confirmacao. "
            "PICO: use clipadores_distintos, NUNCA clipes_do_mesmo_momento. O que "
            "vale e quantas PESSOAS diferentes clipara o mesmo trecho: um unico "
            "clipador apertando 4 vezes nao e o publico votando, e isso acontece. "
            "2 ou mais clipadores distintos = teve pico de verdade; 4 ou mais = "
            "momento forte. Use no criterio de pico em vez de adivinhar pelo "
            "titulo. 1 clipador nao significa momento fraco, significa ausencia "
            "de informacao: nesse caso pontue pela transcricao. "
            "vod_longo=true: VOD acima de 26,5h, onde --download-sections trava; "
            "o download tem que ser por segmentos. "
            "contexto_recuperavel=true nao garante download: VOD de inscritos "
            "so aparece como falha na hora de baixar. "
            "TRACAO: pontue por tracao_relativa (0-100, percentil dentro do "
            "proprio grupo de origem), NUNCA por views_por_hora bruto. Comparar "
            "views entre BR e internacional e invalido: reflete tamanho de "
            "audiencia na Twitch, nao qualidade do momento. "
            "TITULO ORIGINAL RUIM nao e momento ruim: titulo vago ('????', '1 de "
            "qi') so significa que o streamer nao nomeou o clipe. Nesses casos "
            "voce nao tem como julgar o conteudo -- marque o candidato como "
            "'precisa inspecao visual' em por_que_performa em vez de descartar. "
            "Nao reescreva links. Saturacao e permissao nao vem desta fonte: "
            "trate como desconhecido salvo informacao externa."
        ),
    }

    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"[ok] {len(br)} BR + {len(intl)} internacionais -> {a.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()

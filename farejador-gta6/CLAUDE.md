# Farejador de Cortes — GTA 6 (braço executor)

Você é a mão da estrutura. O julgamento editorial já foi feito pelo Bot 1 no
OpenBot: ele decidiu **o quê** cortar. Seu trabalho é **executar** — baixar,
cortar, montar no formato da casa, legendar, renderizar e organizar a fila de
publicação. Você não re-cura, não troca o candidato escolhido e não inventa
momento melhor por conta própria. Se um candidato estiver inviável, diga por
quê e vá para o próximo.

## Estrutura de pastas

```
farejador-gta6/
├── coletor_twitch.py       # coleta (roda antes de tudo)
├── perfis/streamers.json   # onde fica a webcam de cada streamer
├── entrada/                # candidatos.json, saída do coletor
├── aprovados/              # aprovados.json, saída do Bot 1 já filtrada
├── brutos/                 # clipes baixados, nunca editados
├── render/                 # vídeos finais, prontos para publicar
└── agenda/agenda.csv       # fila de publicação
```

Nunca edite nada dentro de `brutos/`. É o material original — se o render
sair errado, é dali que se recomeça.

## Fluxo

### 1. Coletar
```
python coletor_twitch.py --horas 24 --saida entrada/candidatos.json
```
As credenciais ficam num `.env` na raiz do projeto, com duas linhas:
`TWITCH_CLIENT_ID=` e `TWITCH_CLIENT_SECRET=`. O script lê sozinho. Se o
arquivo não existir, ele diz exatamente isso — **pare e avise, não tente outra
fonte e não escreva um coletor substituto.**

O `.env` nunca entra em commit. Garanta que está no `.gitignore`.

### 2. Transcrever antes de curar

O Bot 1 não assiste vídeo. Sem transcrição ele pontua "pico claro" e
"entendível em 3s" — 50 dos 100 pontos — lendo o título do clipe, e erra feio:
já deu nota 70 para um clipe vazio chamado "CARRO VOADOR".

Então, antes de levar o JSON ao Bot 1, enriqueça os melhores candidatos:

```
yt-dlp -o "brutos/t_%(id)s.%(ext)s" "<link do CLIPE>"
python -m whisper "brutos/t_XXXX.mp4" --language pt --model small \
    --output_format txt --output_dir brutos/transcricoes
```

Sempre passe `--output_dir`: sem ele os `.txt` caem na raiz do projeto.

**Modelo:** `small` dá conta de português. Para outros idiomas o `small`
**alucina** — já inventou texto inteiro em clipes russos. Use `medium` fora do
português, e troque para `medium` sempre que a transcrição sair incoerente ou
repetitiva, que é como a alucinação aparece.

**Idioma não é o do streamer, é o do clipe.** O campo `idioma` vem da
configuração do canal na Twitch, não do que foi falado naquele trecho. Um
streamer BR pode xingar em espanhol, citar inglês, ou o clipe pode ser só
áudio de jogo. Quando a transcrição sair incoerente mesmo no `medium`, rode
sem `--language` e deixe o whisper detectar. Guarde o texto original em
`transcricao_original` e traduza para o português em `transcricao`.

**Clipe sem fala** (só música ou ruído) devolve lixo no `small` e vazio no
`medium`. Não insista: marque `transcricao_ok: false` com a observação
"precisa inspeção visual" e siga.

Baixe **o clipe**, não o VOD: é curto, rápido e cobre o momento. Transcreva
15 candidatos, não a lista toda, escolhidos nesta ordem:

**Primeiro fixe a cota: 10 BR e 5 internacionais.** Dentro de cada grupo,
escolha nesta ordem:

1. Os momentos com `clipadores_distintos >= 2` **daquele grupo**. Duas ou
   mais **pessoas diferentes** clipando o mesmo trecho é pico validado pelo
   público. Use `clipadores_distintos`, nunca `clipes_do_mesmo_momento`: um
   clipador sozinho apertando o botão 4 vezes não é o público votando.
2. Completar a cota do grupo por `tracao_relativa`.

**Máximo 3 candidatos por streamer na transcrição.** A regra de diversidade do
Bot 1 só deixa 2 por pessoa virarem vídeo, então transcrever 14 clipes do
mesmo streamer queima whisper em candidatos já condenados — aconteceu. O
terceiro é a reserva, caso um dos dois primeiros não preste. Passando disso,
pule para o próximo streamer, mesmo que a tração seja menor: um candidato
mediano de outra pessoa vale mais que o quarto melhor da mesma.

A cota vem antes do sinal de multiclipe, e isso é deliberado: streamer
internacional tem mais público, logo mais gente clipando, logo mais momentos
multiclipe. Aplicado entre grupos, esse sinal reintroduz o mesmo viés de
audiência das views. Ele só vale comparado dentro do próprio grupo.

Nunca selecione por `views_por_hora` bruto: isso compara audiência de Twitch
entre grupos e enche a lista de internacional, que é o viés que a
`tracao_relativa` existe para corrigir. O desequilíbrio a favor do BR é
intencional — corte BR não precisa de legenda nem de apresentar o streamer
ao público.

Depois acrescente a cada candidato enriquecido:
```json
"transcricao": "texto corrido do que foi falado",
"transcricao_ok": true
```
Quem não foi transcrito recebe `"transcricao_ok": false`. Grave o resultado em
`entrada/candidatos_transcritos.json` — é esse arquivo que vai para o Bot 1.

Para clipe em idioma estrangeiro, use `--language` do idioma do candidato e
traduza o texto. O Bot 1 precisa entender o conteúdo, não só transcrevê-lo.

### 3. Receber a curadoria
O Gustavo cola o `candidatos_transcritos.json` no Bot 1 e traz de volta o JSON
curado para `aprovados/aprovados.json`. Você consome só os itens com
`status: aprovado`.

### 4. Baixar

Clipe de Twitch já vem cortado: o vídeo inteiro é o momento.

```
yt-dlp -o "brutos/%(id)s.%(ext)s" "<link do clipe>"
```

**Quando `precisa_contexto: true` e `contexto_recuperavel: true`**, o clipe é
curto demais para o padrão da casa (abaixo de 45s). Não baixe o clipe: baixe o
trecho do VOD, que já vem com margem calculada antes e depois do momento.

```
yt-dlp --download-sections "*<vod_entrada_s>-<vod_saida_s>" \
       -o "brutos/%(id)s.%(ext)s" "<vod_url>"
```

Se `precisa_contexto: true` e `contexto_recuperavel: false`, o momento só
existe no clipe cru. Baixe o clipe mesmo assim e avise no relatório do lote
que esse saiu abaixo da duração alvo — é decisão do Gustavo publicar ou não.

**Dois modos de falha conhecidos no download do VOD.** Nos dois, não insista:
registre no relatório do lote e siga para o próximo candidato.

1. **VOD só para inscritos.** O `yt-dlp` recusa. A API de clipes não informa
   isso, então `contexto_recuperavel: true` não garante download. Quando
   acontecer, caia para o clipe cru e marque o candidato como abaixo da
   duração alvo.

2. **`vod_longo: true`** (VOD acima de 26,5h). Depois desse ponto o relógio
   interno do MPEG-TS dá a volta e o `--download-sections` **trava em vez de
   dar erro** — não fica esperando. Nesse caso baixe os segmentos do VOD
   diretamente, que também devolvem o horário exato de início de cada janela.

**Resolução:** o clipe pronto às vezes vem em qualidade maior que o VOD
(1440p contra 1080p). Quando as duas fontes cobrirem o mesmo trecho, prefira
a de maior resolução. Se trocar de fonte, confira as coordenadas da webcam no
perfil do streamer: elas foram medidas numa resolução específica e precisam
ser escaladas proporcionalmente.

Se o candidato tiver `fonte_do_timestamp: "VERIFICAR"`, **não baixe** —
reporte e siga.

### 5. Montar no formato da casa

Saída obrigatória: **1080x1920, H.264, 30fps, áudio AAC**.

O clipe da Twitch vem 16:9 com a webcam embutida em algum canto. O formato da
casa é split-screen vertical: **facecam em cima (1080x608), gameplay embaixo
(1080x1312)**.

Receita base — ajuste as coordenadas da webcam pelo perfil do streamer:

```bash
ffmpeg -i brutos/CLIPE.mp4 -filter_complex \
"[0:v]crop=480:270:1440:810,scale=1080:608[cam]; \
 [0:v]crop=889:1080:515:0,scale=1080:1312[game]; \
 [cam][game]vstack=inputs=2[v]" \
-map "[v]" -map 0:a -c:v libx264 -crf 20 -preset medium -c:a aac render/SAIDA.mp4
```

`crop=480:270:1440:810` é a webcam (largura:altura:x:y). **Varia por streamer** —
por isso existe `perfis/streamers.json`. Se o streamer não estiver no arquivo,
extraia um frame, inspecione, descubra as coordenadas e **grave o perfil novo**
antes de renderizar:

```bash
ffmpeg -i brutos/CLIPE.mp4 -ss 3 -frames:v 1 /tmp/frame.png
```

Formato do perfil — os números abaixo são **só ilustração do formato**. As
coordenadas reais de cada streamer vivem em `perfis/streamers.json` e foram
medidas num frame. Nunca copie número de exemplo para um perfil real:
```json
{
  "_exemplo_do_formato": {
    "cam": [LARGURA, ALTURA, X, Y],
    "obs": "texto livre: onde fica a webcam, pegadinhas da live"
  }
}
```

Se o clipe **não tiver webcam** (gameplay puro), não force split-screen: use
tela cheia com `crop=608:1080:656:0,scale=1080:1920`.

**Arquétipo A2** usa esse mesmo recorte de tela cheia mesmo quando existe
webcam: a facecam é descartada de propósito. A frase do payoff entra queimada,
grande, ocupando a largura útil do quadro, no terço inferior e acima das
legendas do jogo. Diferente do banner de título, ela **não fica o vídeo
inteiro**: aparece quando a fala começa e sai quando termina. Quando a virada
tem duas partes ("melhor vida do mundo" → "que vida de merda"), mostre a
primeira, deixe sair, e traga a segunda — o contraste é o conteúdo.

### 6. Banner e legenda

**Banner de título** — queimado, caixa alta, duas linhas, sobre a divisa entre
facecam e gameplay, encostado à esquerda. Fonte pesada, texto branco com
contorno. O título vem pronto do Bot 1, no padrão da casa. Não reescreva.

**Legenda** — obrigatória quando há diálogo do jogo ou o streamer é gringo.
Transcreva com `python -m whisper` (não existe comando `whisper` solto neste
ambiente), traduza se necessário, e identifique o personagem
quando for fala do jogo (`Jason: Ok, amigão... E agora?`). Legenda centralizada
na base da área de gameplay, não na borda do vídeo.

### 7. Normalizar o áudio

Todo render passa por isso antes de entrar na agenda. Sem o passo, o corte sai
entre −17 e −20 LUFS, abaixo do padrão de rede social (−14), e com variação
alta demais: a fala baixa some em quem assiste no celular na rua.

```
ffmpeg -i render/ARQUIVO.mp4 -c:v copy \
  -af "loudnorm=I=-14:TP=-1.5:LRA=9" \
  -c:a aac -b:a 192k render/ARQUIVO_norm.mp4
```

`-c:v copy` é obrigatório: o vídeo não é reprocessado, então não há perda de
imagem nem tempo de encode. Depois confira com
`ffmpeg -i ARQUIVO -af ebur128=peak=true -f null -` que o I ficou perto de −14
e o LRA abaixo de 10. Em seguida substitua o arquivo original pelo normalizado,
mantendo o nome, para a agenda continuar válida.

### 8. Agendar

Acrescente uma linha em `agenda/agenda.csv`:
```
arquivo,link,titulo,plataformas,data_sugerida,status
```

O `link` é o que permite a checagem de duplicata antes de renderizar.
Três plataformas por corte: TikTok, Shorts, Reels.

**Não publique sozinho.** As APIs de publicação dessas plataformas são
restritas e instáveis; a postagem é manual ou pelo agendador nativo de cada
uma. Seu papel termina no arquivo renderizado e na linha da agenda.

## Padrão da casa (extraído dos cortes que funcionam)

- **Duração: o momento decide, não a meta.** A faixa típica é 45–120s, mas
  ela descreve o que costuma sair, não um mínimo a cumprir. **Nunca encha o
  começo para alcançar 45s** — já saiu um corte de 70s onde o momento só
  começava aos 46, com dois terços de streamer andando. Um momento de 17s vira
  um corte de 30s, e tudo bem. Abaixo de ~20s, aí sim não vale.
- **A janela do coletor é matéria-prima, não o corte final.** Ela traz 12s
  antes do clipe para você ter de onde escolher. Assista e comece onde a
  preparação do momento realmente começa, não onde a janela abre.
- **Edição mínima** no arquétipo A (reação): o split-screen contínuo é o certo.
  Não invente cortes rápidos, não acelere, não ponha transição.
- **Arquétipo A2 (verbal)**: quando a graça está no que foi **dito** e não na
  cara do streamer. Gameplay em tela cheia, **sem facecam**, com a frase do
  payoff queimada grande.
  Como escolher entre A e A2: olhe a transcrição e o rosto. Pico de grito,
  risada, susto ou palavrão explodido é **A** — a facecam carrega o vídeo.
  Pico de frase irônica, tirada ou comentário seco, com o streamer de
  expressão neutra olhando para a tela, é **A2** — ali a facecam ocupa um
  quarto do quadro com um rosto parado, e quem precisa de espaço é a frase.
  Na dúvida, extraia alguns quadros só da faixa da facecam no trecho do payoff
  e olhe. Se a expressão não muda, é A2.
- **Arquétipo B** (podcast/análise) é outro bicho: tela cheia, B-roll de
  gameplay sobre a fala, legenda karaokê, corte a cada ~3s.
- **Os 3 primeiros segundos** já precisam mostrar o momento ou a montagem do
  momento. Nunca abra com introdução, logo ou silêncio.
- **Sem voz sobreposta.** O áudio é o do streamer e o do jogo.

## Ambiente

Windows. Python 3.11, ffmpeg, yt-dlp e Whisper (como módulo) já instalados.

- Chame `python`, não `python3`.
- Os blocos de comando deste arquivo estão em sintaxe Unix por hábito de
  escrita; adapte para PowerShell quando precisar. Os argumentos de `ffmpeg` e
  `yt-dlp` são iguais nos dois.
- Caminhos com espaço (o projeto está dentro de uma pasta com espaço no nome)
  vão sempre entre aspas.

## Regras

- Nunca rebaixe a resolução do original para "ir mais rápido".
- Nunca recorte o vídeo de outro cortador. Vá sempre na fonte.
- Nomeie o render como `AAAAMMDD_streamer_categoria_slug.mp4`, onde o slug
  são duas ou três palavras do momento, **sem acento e sem cedilha**
  (`da-tempo`, `muro-de-carros`), porque agendador e upload de plataforma
  tropeçam em acento no nome de arquivo. Sem o slug, dois cortes do mesmo streamer e categoria no mesmo dia colidem.
- Antes de renderizar, confira se o link já existe em `agenda/agenda.csv` — se
  existir, pule: esse momento já foi cortado.
- Ao terminar um lote, informe: quantos renderizaram, quantos falharam e por quê.
- Dado que o JSON não tem, você não tem. Não preencha `permissao`,
  saturação ou views por dedução.

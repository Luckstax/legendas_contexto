# Legendas — Contexto da série

Ferramenta única que junta duas outras: o **TG downloader** (baixa vídeos de um canal do Telegram) e o **context_builder** (transcreve os episódios e gera um `context.json` com personagens, glossário e termos que não devem ser traduzidos). O resultado serve de contexto para uma IA que cria legendas.

## O que faz

Três etapas, cada uma pode ser ligada ou desligada na tela principal:

1. **Baixar do Telegram.** Lê os vídeos de um canal e salva na pasta de vídeos. Pula o que já foi transcrito.
2. **Transcrever (Whisper).** Extrai o áudio de cada vídeo e grava um `.txt` com timestamps por episódio.
3. **Analisar (LM Studio).** Lê as transcrições, faz a análise por chunks e gera o `context.json` final.

Na tela principal você escolhe, a cada execução, se os vídeos ficam guardados ou se são apagados logo após a transcrição. A análise usa só as transcrições, então funciona mesmo com os vídeos apagados.

## Instalação

```bash
pip install -r requirements.txt
```

Para usar GPU NVIDIA no Whisper, instale também o PyTorch com CUDA (veja `requirements.txt`).

Você também precisa do [LM Studio](https://lmstudio.ai) instalado, com um modelo baixado.

## Configuração

Abra o programa e clique em **⚙ Configurações**. Ali você preenche:

- **Telegram** (vai para o `.env`): `API_ID`, `API_HASH`, canal, limite de mensagens, nome da sessão. O `API_ID` e o `API_HASH` vêm de https://my.telegram.org → *API Development Tools*.
- **Série e pastas** (vai para o `config.json`): nome da série, pastas de vídeos, transcrições e análises, e os caminhos dos arquivos de saída.
- **Whisper** e **LM Studio**: modelos, idioma, dispositivo e parâmetros.

Você também pode copiar `.env.example` para `.env` e `config.example.json` para `config.json` e editar à mão.

Caminhos relativos são resolvidos a partir da pasta do projeto. Nenhum caminho absoluto fica no código.

## Uso

```bash
python main.pyw
```

1. Marque as etapas que quer rodar.
2. Escolha se os vídeos ficam guardados ou são apagados após a transcrição.
3. Clique em **▶ Iniciar**. No primeiro login, o Telegram pede o código (e a senha 2FA, se houver).

Durante o download, **⏭ Pular vídeo atual** pula só o arquivo em andamento. **■ Parar** interrompe depois da etapa atual.

## Estrutura

```
legendas_contexto/
  main.pyw               ponto de entrada
  gui.py                 janela principal e tela de Configurações
  settings.py            leitura e escrita do .env e do config.json
  pipeline.py            orquestra as três etapas
  telegram_fetch.py      download de vídeos do Telegram (Telethon)
  transcricao.py         transcrição com faster-whisper
  audio.py               extração de áudio (PyAV)
  analise.py             análise por chunks e consolidação do context.json
  lms_client.py          integração com o LM Studio
  controle.py            controle compartilhado: progresso, parada por etapa e desligamento
  logger.py              log em tela, em arquivo e estatísticas
  utils.py               utilitários
  .env.example           modelo das credenciais (vai pro Git)
  config.example.json    modelo da configuração (vai pro Git)
```

## Saída

```
dados/
  videos/                vídeos baixados (se você escolheu manter)
  transcricoes/          um .txt por episódio (cache da etapa 2)
  analises/              um .json por episódio (cache da etapa 3)
  baixados.txt           nomes dos vídeos já baixados do Telegram (um por linha)
  context.json           resultado final
  relatorio.txt          log da última execução
```

Cada etapa usa cache. Se o programa parar no meio, a próxima execução continua de onde parou, sem refazer o que já está pronto.

## Segurança

- O `.env` guarda as credenciais da sua conta Telegram e o arquivo de sessão dá acesso à conta. Nenhum dos dois deve ir para o Git. O `.gitignore` já os exclui.
- Se algum deles já foi versionado, revogue as sessões ativas em Telegram → Privacidade e Segurança → Sessões Ativas e gere um novo `API_HASH`.

## Limitações conhecidas

- A ordem dos episódios vem do nome do arquivo (`S01E01` ou `1x01`). Vídeos sem esse padrão vão para o fim da fila. Por isso é útil que a legenda da mensagem no Telegram contenha o código do episódio.
- A qualidade do contexto depende da transcrição. Nomes próprios em áudio dublado podem sair errados, então revise o `context.json` antes de usar.

# Servidor HTTP com sockets TCP

## 1. O projeto e como rodar

Este protótipo para a disciplina de Redes de Computadores recebe requisições HTTP em um socket TCP, inicia uma thread por conexão e mantém uma lista de cadastros por sessão. O formulário usa os campos `nome` e `descricao`. O servidor usa apenas a biblioteca padrão do Python.

Na pasta que contém `server.py`, execute com Python 3:

```sh
python server.py
```

Abra [http://localhost:8080/](http://localhost:8080/) no navegador. A escuta em `0.0.0.0:8080` aceita conexões em todas as interfaces IPv4 da máquina. Use `Ctrl+C` para encerrar o servidor.

| Requisição | Resposta |
| --- | --- |
| `GET /` | `200 OK` com o formulário e a lista da sessão atual. |
| `POST /cadastrar` | Salva os campos na sessão e responde `303 See Other` com `Location: /`. O navegador busca a lista atualizada com `GET /`. |
| `GET /lento` | Espera cinco segundos e responde `200 OK` com texto simples. |
| Outros métodos e caminhos | `404 Not Found` para combinações de método e caminho fora das rotas acima. |

## 2. Fluxo do servidor

```text
socket() -> bind() -> listen() -> accept()             [servir]
                                     |
                                     +--> Thread.start()    [servir]
                                     |         |
                                     |         v
                                     |    atender_cliente()
                                     |         |
                                     |         v
                                     |    recv()            [ler_requisicao]
                                     |         |
                                     |         v
                                     |    sessao e rota     [obter_sessao, responder_rota]
                                     |         |
                                     |         v
                                     |    sendall()         [enviar_resposta]
                                     |         |
                                     |         v
                                     |    close()           [atender_cliente]
                                     v
                                  accept()                  [servir, próxima conexão]
```

`socket()` cria o endpoint IPv4/TCP, `bind()` associa o endereço e a porta, e `listen()` coloca o socket em modo de escuta. `accept()` entrega um segundo socket, específico para o cliente, e o endereço remoto. O socket de escuta continua disponível para o próximo `accept()` enquanto a thread usa o socket do cliente.

O TCP fornece um fluxo de bytes. A primeira leitura pode trazer uma parte dos cabeçalhos ou os cabeçalhos junto com o início do corpo. O servidor acumula bytes até a linha vazia (`\r\n\r\n`) e completa a leitura do corpo pelo `Content-Length`, quando informado. A resposta também usa uma linha vazia para separar cabeçalhos e corpo. O `Content-Length` da resposta conta bytes em UTF-8, e `Connection: close` indica que a conexão termina após o envio.

## 3. Mapa das funções

| Função | Entrada | O que faz | Saída |
| --- | --- | --- | --- |
| `servir` | Nenhum argumento. Usa `HOST` e `PORT`. | Configura o log, prepara a escuta e cria uma thread nomeada para cada conexão aceita. | Fecha o socket de escuta ao encerrar. |
| `atender_cliente` | Socket e endereço do cliente. | Lê a requisição, identifica a sessão, registra os eventos e chama a rota. Fecha o socket em `finally`. | Uma resposta HTTP quando há uma requisição. |
| `ler_requisicao` | Socket do cliente. | Lê método, alvo e versão, extrai o caminho, normaliza os nomes dos cabeçalhos para minúsculas e lê o corpo. | Tupla com método, caminho, cabeçalhos e corpo em bytes. Devolve `None` se o cliente fecha antes de enviar dados. |
| `obter_sessao` | Valor do cabeçalho `Cookie`, ou texto vazio. | Procura o `SESSIONID` e cria a lista sob a trava quando necessário. | Tupla com o ID, um indicador de que a lista foi criada e outro de que deve enviar `Set-Cookie`. |
| `responder_rota` | Socket, método, caminho, corpo, ID da sessão e cabeçalhos extras. | Escolhe a rota, processa o formulário com `parse_qs` quando necessário e chama `enviar_resposta`. | Resposta da rota no socket. |
| `renderizar_pagina` | ID da sessão. | Copia a lista sob a trava, escapa os campos com `html.escape` e substitui `{{ITENS}}` no template. | HTML como texto. |
| `enviar_resposta` | Socket, status, texto, cabeçalhos extras e tipo do conteúdo. | Codifica o corpo, monta a linha de status e os cabeçalhos HTTP/1.1 e chama `sendall()`. | Bytes enviados ao cliente. |
| `registrar` | Endereço do cliente, ID da sessão e mensagem. | Escreve no terminal com horário, nome da thread, IP, porta e prefixo do ID. | Uma linha de log. |

## 4. Teoria para os slides

**Thread.** É um fluxo de execução dentro de um processo. As threads compartilham a memória do servidor. Uma thread por conexão permite que a thread principal volte ao `accept()` enquanto outra espera bytes em `recv()` ou dorme em `/lento`. Duas requisições podem progredir no mesmo intervalo de tempo.

**Race condition (condição de corrida).** Acontece quando o resultado depende da ordem em que threads acessam um estado compartilhado. Se duas threads verificassem ao mesmo tempo a ausência de um mesmo ID, ambas poderiam tentar criar a lista e uma inicialização poderia sobrescrever a outra. O `Lock` protege a consulta e a criação da sessão como um único bloco. Ele também protege a inclusão de itens e a cópia da lista. O servidor libera a trava antes de ler o template, enviar a resposta ou esperar em `/lento`.

**HTTP sem estado e sessão.** O HTTP trata cada requisição de forma independente. A sessão permite à aplicação associar várias requisições à mesma lista. O dicionário `sessoes` guarda uma lista de registros por identificador. Um cadastro tem a forma `{"nome": "Lápis", "descricao": "Azul"}`.

**Cookie.** Em uma requisição sem `SESSIONID`, o servidor cria o ID com `secrets.token_hex(16)` e devolve `Set-Cookie: SESSIONID=<id>; Path=/; HttpOnly`. Em seguida, o navegador envia `Cookie: SESSIONID=<id>` nas requisições ao mesmo host. `Path=/` cobre todas as rotas e `HttpOnly` impede que scripts da página leiam o cookie. O ID identifica a lista no servidor. Se o navegador envia um ID que o servidor ainda não conhece, o servidor começa uma lista vazia para esse ID.

## 5. Roteiro de teste

### Separar duas sessões no navegador

1. Abra a página inicial em dois navegadores diferentes ou em uma janela normal e uma anônima.
2. Na primeira janela, cadastre `Lápis` com descrição `Azul`. Na segunda, cadastre `Caderno` com descrição `Verde`.
3. Recarregue as duas páginas. A primeira deve listar apenas `Lápis` e a segunda, apenas `Caderno`.

### Repetir com curl

Execute em outro terminal. No PowerShell, use `curl.exe` se `curl` for um alias. Use `a.txt` e `b.txt` novos para começar com sessões vazias.

```sh
curl -i http://localhost:8080/
curl -i -c a.txt -b a.txt http://localhost:8080/
curl -i -c a.txt -b a.txt -d "nome=x&descricao=y" http://localhost:8080/cadastrar
curl -i -c a.txt -b a.txt http://localhost:8080/
curl -i -c b.txt -b b.txt http://localhost:8080/
curl -i -c b.txt -b b.txt -d "nome=z&descricao=w" http://localhost:8080/cadastrar
curl -i -c b.txt -b b.txt http://localhost:8080/
curl -i -c a.txt -b a.txt http://localhost:8080/
```

A primeira resposta deve trazer `Set-Cookie`. Cada `POST` deve devolver `303` e `Location: /`. Na listagem, `a.txt` deve mostrar apenas `x` com descrição `y` e `b.txt` deve mostrar apenas `z` com descrição `w`.

### Demonstrar as threads

1. Deixe o terminal do servidor visível. Abra `/lento` em duas abas quase ao mesmo tempo. Para o navegador fazer dois pedidos separados, use `http://localhost:8080/lento?aba=1` e `http://localhost:8080/lento?aba=2`. O servidor roteia ambas pelo caminho `/lento`.
2. Observe duas linhas `GET /lento` com nomes de thread diferentes antes da primeira linha `thread terminou` dessas conexões. As duas abas devem terminar aproximadamente cinco segundos após o início, se os pedidos começarem juntos.
3. Confira que cada requisição registra IP e porta remotos, o prefixo da sessão e o caminho. Eventos de criação de sessão e fim de thread aparecem em linhas próprias. Duas abas do mesmo perfil normalmente compartilham o cookie, mas cada conexão tem sua própria thread.

```text
[14:32:10] [Thread-9] cliente=127.0.0.1:54109 sessao=ab12cd34... GET /lento
[14:32:10] [Thread-10] cliente=127.0.0.1:54110 sessao=ab12cd34... GET /lento
[14:32:15] [Thread-9] cliente=127.0.0.1:54109 sessao=ab12cd34... thread terminou
[14:32:15] [Thread-10] cliente=127.0.0.1:54110 sessao=ab12cd34... thread terminou
```

## 6. O que observar no Wireshark

1. Antes de fazer o primeiro `GET`, inicie a captura na interface de **loopback**. No Windows com Npcap, procure **Adapter for loopback traffic capture**. Faça um acesso com uma sessão nova e depois outro com o mesmo navegador ou cookie jar. [Captura em loopback no Wireshark](https://wiki.wireshark.org/CaptureSetup/Loopback)
2. Na barra de **filtros de exibição**, use `tcp.port == 8080`. No começo de cada conexão, procure o `SYN` do cliente, o `SYN, ACK` do servidor e o `ACK` do cliente. Essas três etapas estabelecem a conexão antes da troca HTTP.
3. Aplique `http` para focar nas mensagens HTTP. Use `http.set_cookie` para localizar `Set-Cookie` na primeira resposta e `http.cookie` para localizar `Cookie` na requisição seguinte. Se a porta 8080 aparecer apenas como TCP, selecione um pacote com dados e use **Analyze → Decode As…** para definir HTTP. [Campos HTTP do Wireshark](https://www.wireshark.org/docs/dfref/h/http.html), [implementação de `http.set_cookie`](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-http.c)
4. Clique com o botão direito no pacote e escolha **Follow → TCP Stream** para ler a requisição e a resposta juntas. Como o servidor fecha a conexão após cada resposta, siga o stream da primeira visita para ver `Set-Cookie` e outro stream da mesma sessão para ver `Cookie`. [Guia do Follow TCP Stream](https://www.wireshark.org/docs/wsug_html_chunked/ChAdvFollowStreamSection.html)

## 7. Limitações e troca do HTML

- Os cadastros vivem na memória e são perdidos ao reiniciar o processo. O servidor não expira sessões.
- O tráfego é HTTP sem HTTPS. Uma captura de rede consegue ler o conteúdo e os cookies.
- O protótipo atende uma requisição por conexão. O parser usa `Content-Length` para ler corpos e não implementa transferência em blocos (`chunked`).

Para substituir a página, edite `templates/index.html` em UTF-8. Mantenha o formulário com `method="post"`, `action="/cadastrar"` e campos com `name="nome"` e `name="descricao"`. Preserve `{{ITENS}}` dentro de uma lista, pois o servidor substitui esse marcador por elementos `<li>`. Salve o arquivo e recarregue `/`. O servidor lê o template em cada `GET /`, então a alteração aparece sem reiniciar.

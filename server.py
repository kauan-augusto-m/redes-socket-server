"""Servidor HTTP didático sobre sockets TCP, com threads e sessões em memória."""

import html
import logging
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs


HOST = "0.0.0.0"
PORT = 8080
TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"

# Todas as threads usam este dicionário, então os acessos passam pela mesma trava.
sessoes = {}
trava_sessoes = threading.Lock()


def ler_requisicao(conexao):
    """Lê método, caminho, cabeçalhos e corpo de uma requisição HTTP."""
    dados = bytearray()
    fim_cabecalhos = b"\r\n\r\n"

    # O TCP entrega um fluxo de bytes. Uma chamada a recv pode trazer só uma parte.
    while fim_cabecalhos not in dados:
        trecho = conexao.recv(4096)
        if not trecho:
            if not dados:
                return None
            raise ValueError("Cabeçalhos incompletos")
        dados.extend(trecho)

    bloco, _, corpo = dados.partition(fim_cabecalhos)
    primeira_linha, *linhas = bloco.decode("iso-8859-1").split("\r\n")
    metodo, alvo, versao = primeira_linha.split()
    if versao not in ("HTTP/1.0", "HTTP/1.1"):
        raise ValueError("Versão HTTP fora do escopo do protótipo")
    caminho = alvo.split("?", 1)[0]

    cabecalhos = {}
    for linha in linhas:
        nome, separador, valor = linha.partition(":")
        if not separador or not nome.strip():
            raise ValueError("Cabeçalho inválido")
        cabecalhos[nome.strip().lower()] = valor.strip()

    tamanho = cabecalhos.get("content-length", "0")
    if not tamanho.isascii() or not tamanho.isdecimal():
        raise ValueError("Content-Length inválido")
    tamanho = int(tamanho)

    # O primeiro recv pode já ter trazido parte do corpo. Lemos só o que falta.
    while len(corpo) < tamanho:
        trecho = conexao.recv(min(4096, tamanho - len(corpo)))
        if not trecho:
            raise ValueError("Corpo incompleto")
        corpo.extend(trecho)

    return metodo, caminho, cabecalhos, bytes(corpo[:tamanho])


def obter_sessao(cookie):
    """Retorna o ID, se a lista foi criada e se a resposta deve enviar o cookie."""
    id_sessao = None
    for campo in cookie.split(";"):
        nome, _, valor = campo.strip().partition("=")
        if nome == "SESSIONID" and valor:
            id_sessao = valor
            break

    enviar_cookie = id_sessao is None
    # Escolher um ID livre e criar sua lista precisam ocorrer sob a mesma trava.
    with trava_sessoes:
        if enviar_cookie:
            id_sessao = secrets.token_hex(16)
            while id_sessao in sessoes:
                id_sessao = secrets.token_hex(16)
        criada = id_sessao not in sessoes
        if criada:
            sessoes[id_sessao] = []

    return id_sessao, criada, enviar_cookie


def renderizar_pagina(id_sessao):
    """Insere os itens da sessão no HTML lido do disco a cada visita à página."""
    # A cópia permite montar o HTML sem segurar a trava enquanto outra thread cadastra.
    with trava_sessoes:
        itens = sessoes[id_sessao].copy()

    # O escape mantém os valores digitados como texto dentro da página.
    linhas = []
    for item in itens:
        nome = html.escape(item["nome"])
        descricao = html.escape(item["descricao"])
        linhas.append(f"<li><strong>{nome}</strong> — {descricao}</li>")

    lista = "\n".join(linhas) or "<li>Nenhum item cadastrado.</li>"
    return TEMPLATE.read_text(encoding="utf-8").replace("{{ITENS}}", lista)


def enviar_resposta(conexao, status, texto, extras=(), tipo="text/plain"):
    """Monta a resposta HTTP/1.1 e envia cabeçalhos e corpo pelo socket."""
    # O tamanho informado ao cliente é o número de bytes após a codificação UTF-8.
    corpo = texto.encode("utf-8")
    linhas = [
        f"HTTP/1.1 {status}",
        f"Content-Type: {tipo}; charset=utf-8",
        f"Content-Length: {len(corpo)}",
        "Connection: close",
    ]
    linhas.extend(f"{nome}: {valor}" for nome, valor in extras)
    cabecalhos = ("\r\n".join(linhas) + "\r\n\r\n").encode("ascii")
    conexao.sendall(cabecalhos + corpo)


def responder_rota(conexao, metodo, caminho, corpo, id_sessao, extras):
    """Executa a rota escolhida pelo método e pelo caminho e envia a resposta."""
    if (metodo, caminho) == ("GET", "/"):
        pagina = renderizar_pagina(id_sessao)
        enviar_resposta(conexao, "200 OK", pagina, extras, tipo="text/html")
    elif (metodo, caminho) == ("POST", "/cadastrar"):
        formulario = parse_qs(corpo.decode("utf-8", errors="replace"), keep_blank_values=True)
        item = {
            "nome": formulario.get("nome", [""])[0],
            "descricao": formulario.get("descricao", [""])[0],
        }
        with trava_sessoes:
            sessoes[id_sessao].append(item)
        # O 303 faz o navegador buscar a lista com GET após o cadastro.
        enviar_resposta(conexao, "303 See Other", "Cadastro salvo.\n", extras + [("Location", "/")])
    elif (metodo, caminho) == ("GET", "/lento"):
        # A pausa fica fora da trava, assim outras conexões continuam sendo atendidas.
        time.sleep(5)
        enviar_resposta(conexao, "200 OK", "Resposta após 5 segundos.\n", extras)
    else:
        enviar_resposta(conexao, "404 Not Found", "Rota não encontrada.\n", extras)


def registrar(endereco, id_sessao, mensagem):
    """Registra um evento com endereço e prefixo do ID para acompanhar a thread."""
    ip, porta = endereco
    prefixo = f"{id_sessao[:8]}..." if id_sessao else "-"
    logging.info("cliente=%s:%s sessao=%s %s", ip, porta, prefixo, mensagem)


def atender_cliente(conexao, endereco):
    """Atende uma requisição na thread da conexão e fecha o socket ao terminar."""
    id_sessao = None
    try:
        try:
            requisicao = ler_requisicao(conexao)
        except ValueError:
            registrar(endereco, id_sessao, "requisição inválida")
            enviar_resposta(conexao, "400 Bad Request", "Requisição inválida.\n")
            return
        if requisicao is None:
            return

        metodo, caminho, cabecalhos, corpo = requisicao
        id_sessao, criada, enviar_cookie = obter_sessao(cabecalhos.get("cookie", ""))
        registrar(endereco, id_sessao, f"{metodo} {caminho}")
        if criada:
            registrar(endereco, id_sessao, "sessão criada")

        extras = []
        if enviar_cookie:
            extras.append(("Set-Cookie", f"SESSIONID={id_sessao}; Path=/; HttpOnly"))
        responder_rota(conexao, metodo, caminho, corpo, id_sessao, extras)
    except OSError as erro:
        registrar(endereco, id_sessao, f"falha de E/S: {erro}")
    finally:
        # Fecha inclusive se o cliente desconectar ou se a leitura falhar.
        conexao.close()
        registrar(endereco, id_sessao, "thread terminou")


def servir():
    """Escuta em IPv4 e inicia uma nova thread para cada conexão aceita."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(threadName)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        servidor.bind((HOST, PORT))
        servidor.listen()
        logging.info("Servidor escutando em %s:%s", HOST, PORT)
        numero = 1
        while True:
            conexao, endereco = servidor.accept()
            # A thread principal volta ao accept enquanto a nova thread atende o cliente.
            threading.Thread(
                target=atender_cliente,
                args=(conexao, endereco),
                name=f"Thread-{numero}",
                daemon=True,
            ).start()
            numero += 1
    except KeyboardInterrupt:
        logging.info("Encerrando o servidor")
    finally:
        servidor.close()


if __name__ == "__main__":
    servir()

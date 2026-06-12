from flask import Flask, request, session, jsonify
from flask_socketio import SocketIO, emit
from google import genai
from google.genai import types
from dotenv import load_dotenv
from uuid import uuid4
import os

# Carrega as variáveis ocultas do arquivo .env (como a chave da API do Gemini)
load_dotenv()

# Define qual versão da IA vamos usar. O modelo "flash" é rápido e ideal para chatbots.
MODELO = "gemini-3.1-flash-lite"

# Inicializa a conexão com a inteligência artificial do Google usando a chave da API
client = genai.Client(api_key=os.getenv("GENAI_KEY"))

# Cria o nosso aplicativo web principal (o servidor)
app = Flask(__name__)

# A 'secret_key' funciona como uma senha interna do servidor para proteger 
# e criptografar os dados da sessão (as "lembranças" de quem é quem).
app.secret_key = "ch@tb07"

# Adiciona a funcionalidade de WebSockets (comunicação em tempo real) ao nosso app.
# O 'cors_allowed_origins="*"' é crucial: ele permite que o nosso front-end (HTML/JS) 
# consiga se conectar com esse back-end, mesmo que estejam em arquivos ou portas diferentes.
socketio = SocketIO(app, cors_allowed_origins="*")

# Dicionários que funcionam como a "memória temporária" do servidor. 
# Guardam o chat e a persona ativa de cada aluno separadamente usando um ID único.
active_chats = {}
active_personas = {}

def get_user_chat(persona=None):
    """
    Função principal de gerenciamento de usuários.
    Ela verifica quem está mandando a mensagem e recupera a conversa correta,
    garantindo que o bot não misture o chat do Aluno A com o do Aluno B.
    Se a persona for informada e for diferente da persona atual da sessão,
    recria o chat com a nova instrução de sistema.
    """
    
    # Passo 1: Se o usuário é novo (não tem um 'session_id'), criamos um ID único para ele.
    # Usamos o 'uuid4' para gerar um código aleatório impossível de repetir.
    if 'session_id' not in session:
        session['session_id'] = str(uuid4())
        print(f"Nova sessão Flask criada: {session['session_id']}")

    session_id = session['session_id']

    # Passo 2: Se o usuário não tem uma conversa aberta ou a persona mudou...
    if session_id not in active_chats or (persona and active_personas.get(session_id) != persona):
        print(f"Criando ou recriando chat Gemini para session_id: {session_id}")
        try:
            sys_instruction = persona or active_personas.get(session_id) or "Você é um assistente virtual."
            # ...nós criamos uma nova conversa e passamos as instruções (personalidade do artista).
            chat_session = client.chats.create(
                model=MODELO,
                config=types.GenerateContentConfig(system_instruction=sys_instruction)
            )
            # Guardamos essa conversa e a persona correspondente na memória
            active_chats[session_id] = chat_session
            if persona:
                active_personas[session_id] = persona
            print(f"Novo chat Gemini criado e armazenado para {session_id}")
        except Exception as e:
            app.logger.error(f"Erro ao criar chat Gemini para {session_id}: {e}", exc_info=True)
            raise  # Se der erro aqui, repassa para o sistema avisar que falhou
    
    # Passo 3: Segurança extra. Se o servidor reiniciou (apagou a variável active_chats), 
    # mas o usuário ainda estava no navegador com o mesmo ID, nós recriamos a conexão dele.
    if session_id in active_chats and active_chats[session_id] is None:
        print(f"Recriando chat Gemini para session_id existente (estava None): {session_id}")
        try:
            sys_instruction = active_personas.get(session_id) or "Você é um assistente virtual."
            chat_session = client.chats.create(
                model=MODELO,
                config=types.GenerateContentConfig(system_instruction=sys_instruction)
            )
            active_chats[session_id] = chat_session
        except Exception as e:
            app.logger.error(f"Erro ao recriar chat Gemini para {session_id}: {e}", exc_info=True)
            raise

    # Retorna o histórico de mensagens exato daquele usuário.
    return active_chats[session_id]

# Rota simples para verificar se o servidor está rodando.
# Ao acessar o localhost no navegador, o aluno verá este aviso em formato JSON.
@app.route('/')
def root():
    return jsonify({
        "api-websocket": "chatbot",
        "status": "ok"
    })


# ------------------------------------------------------------------
# EVENTOS SOCKET.IO (Onde a mágica do tempo real acontece)
# ------------------------------------------------------------------

@socketio.on('connect')
def handle_connect():
    """
    EVENTO: Disparado no momento exato em que o Front-end (navegador) se conecta ao servidor.
    """
    print(f"Cliente conectado: {request.sid}")
    
    try:
        # Garante a criação do session_id e exibe informações de conexão
        if 'session_id' not in session:
            session['session_id'] = str(uuid4())
        user_session_id = session['session_id']
        print(f"Sessão Flask para {request.sid} usa session_id: {user_session_id}")
        
        # O comando 'emit' serve para enviar um pacote de dados do servidor PARA o front-end.
        emit('status_conexao', {'data': 'Conectado com sucesso!', 'session_id': user_session_id})
    except Exception as e:
        app.logger.error(f"Erro durante o evento connect para {request.sid}: {e}", exc_info=True)
        emit('erro', {'erro': 'Falha ao inicializar a sessão de chat no servidor.'})


@socketio.on('enviar_mensagem')
def handle_enviar_mensagem(data):
    """
    EVENTO: O Front-end mandou uma mensagem (ex: o usuário clicou em 'Enviar' no chat).
    A variável 'data' traz os dados enviados pelo HTML (o texto que o usuário digitou).
    """
    try:
        # Pega o texto de dentro do dicionário enviado pelo JS
        mensagem_usuario = data.get("mensagem")
        app.logger.info(f"Mensagem recebida de {session.get('session_id', request.sid)}: {mensagem_usuario}")

        # Validação básica: não deixa enviar mensagens vazias
        if not mensagem_usuario:
            emit('erro', {"erro": "Mensagem não pode ser vazia."})
            return

        # Separa a persona (enviada pelo front-end antes da mensagem) e o texto real do usuário
        partes = mensagem_usuario.split("\n\nUsuário diz: ", 1)
        if len(partes) == 2:
            persona, texto_usuario = partes
        else:
            persona, texto_usuario = None, mensagem_usuario

        # Puxa o histórico de conversa desse aluno específico, ajustando à persona do artista
        user_chat = get_user_chat(persona=persona)
        if user_chat is None:
            emit('erro', {"erro": "Sessão de chat não pôde ser estabelecida."})
            return

        # ==========================================
        # COMUNICAÇÃO COM O GOOGLE GEMINI
        # ==========================================
        # Aqui o nosso servidor repassa a pergunta (apenas o texto digitado) para a IA do Google...
        resposta_gemini = user_chat.send_message(texto_usuario)

        # ... e aqui extraímos apenas o texto da resposta que o Gemini devolveu.
        # (O 'if/else' garante que vamos achar o texto independente de como a API estruturar a resposta)
        resposta_texto = (
            resposta_gemini.text
            if hasattr(resposta_gemini, 'text')
            else resposta_gemini.candidates[0].content.parts[0].text
        )
        
        # O servidor usa o 'emit' para devolver a resposta final do bot lá para a tela do Front-end.
        emit('nova_mensagem', {"remetente": "bot", "texto": resposta_texto, "session_id": session.get('session_id')})
        app.logger.info(f"Resposta enviada para {session.get('session_id', request.sid)}: {resposta_texto}")

    except Exception as e:
        app.logger.error(f"Erro ao processar 'enviar_mensagem' para {session.get('session_id', request.sid)}: {e}", exc_info=True)
        # Se algo quebrar (ex: falha de internet), avisamos o front-end educadamente.
        emit('erro', {"erro": f"Ocorreu um erro no servidor: {str(e)}"})


@socketio.on('limpar_conversa')
def handle_limpar_conversa():
    """
    EVENTO: O Front-end solicitou limpar a conversa.
    Apagamos o chat_session e a persona para começar do zero na próxima mensagem.
    """
    session_id = session.get('session_id')
    if session_id:
        active_chats.pop(session_id, None)
        active_personas.pop(session_id, None)
        print(f"Conversa limpa no servidor para session_id: {session_id}")


@socketio.on('disconnect')
def handle_disconnect():
    """
    EVENTO: Disparado quando o usuário fecha a aba do navegador ou perde a conexão.
    """
    print(f"Cliente desconectado: {request.sid}, session_id: {session.get('session_id', 'N/A')}")


# Inicia o servidor local. A porta padrão do Flask costuma ser a 5000.
if __name__ == "__main__":
    socketio.run(app, port=6500, debug=True)
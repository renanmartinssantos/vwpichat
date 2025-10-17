import sys
try:
    from flask import Flask, render_template
    from flask_socketio import SocketIO, emit
except Exception as e:
    print("Dependência ausente:", e)
    print("Instale as dependências com: C:/Python313/python.exe -m pip install -r requirements.txt")
    sys.exit(1)

import os
import json
import traceback

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
# usar threading para evitar dependências adicionais (eventlet/gevent)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# modo especial 'Valorant' — quando verdadeiro, usamos um system prompt específico
valorant_mode = False


@app.route('/')
def index():
    return render_template('index.html')


def _get_api_key():
    # procurar várias variáveis de ambiente comuns
    return (
        os.getenv('NVIDIA_API_KEY')
        or os.getenv('API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC')
        or os.getenv('OPENAI_API_KEY')
    )


def _get_client():
    api_key = _get_api_key()
    if OpenAI is None:
        raise RuntimeError('Pacote "openai" não instalado. Verifique requirements.txt')
    if not api_key:
        raise RuntimeError('Chave de API não encontrada. Defina NVIDIA_API_KEY ou API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC')
    return OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)


def stream_to_clients(prompt: str):
    """Chama a API em streaming e retransmite chunks para todos os clientes via Socket.IO."""
    try:
        client = _get_client()
    except Exception as e:
        socketio.emit('message', f"Erro: {e}")
        return

    # obter system prompt configurável da variável de ambiente
    global valorant_mode
    system_prompt = os.getenv('SYSTEM_PROMPT')

    # se estiver no modo Valorant, usar prompt específico
    if valorant_mode:
        system_prompt = (
            "Você é o melhor Radiante de todos. Seja arrogante, direto e sem paciência.\n"
            "Diga que os outros são ruins e que você é superior. Use linguagem coloquial e mostre confiança extrema."
        )
    if not system_prompt:
        # padrão: direto, sincero e sem paciência — permite linguagem coloquial e ocasionalmente palavrões
        system_prompt = (
            "Você é um assistente direto, honesto e sem paciência. Responda de forma sucinta, "
            "sem rodeios; seja franco e use sempre palavrões e linguagem coloquial (pode usar palavrões quando apropriado)."
        )

    try:
        # usar o mesmo formato do snippet: sem tools, stream=True
        completion = client.chat.completions.create(
            model="qwen/qwen3-next-80b-a3b-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            top_p=0.7,
            max_tokens=500,
            stream=True,
        )

        for chunk in completion:
            try:
                # seguindo exatamente o snippet: acessar chunk.choices[0].delta
                if getattr(chunk.choices[0].delta, 'content', None):
                    socketio.emit('message', chunk.choices[0].delta.content)
            except Exception:
                # fallback para dicionário-like
                try:
                    if chunk.get('choices', [None])[0].get('delta', {}).get('content'):
                        socketio.emit('message', chunk.get('choices', [None])[0].get('delta', {}).get('content'))
                except Exception:
                    # ignorar chunk inesperado
                    continue

        # sinalizar fim do stream para os clientes
        socketio.emit('stream_end')

    except Exception as e:
        # enviar erro para os clientes para facilitar depuração
        socketio.emit('message', f"Erro ao conectar à API: {e}")
        # garantir que o cliente seja notificado para reabilitar input
        socketio.emit('stream_end')
        traceback.print_exc()


@socketio.on('message')
def handle_message(msg):
    text = msg.strip() if isinstance(msg, str) else ''
    global valorant_mode
    # ativar modo Valorant
    if text.lower() == 'valorant':
        valorant_mode = True
        socketio.emit('message', 'Modo Valorant ativado.')
        # garantir que o cliente saiba que o stream (local) terminou e reative o input
        socketio.emit('stream_end')
        return

    # comando de sair/desativar modo Valorant
    if text.lower() in ('sair', 'exit', 'quit'):
        if valorant_mode:
            valorant_mode = False
            socketio.emit('message', 'Modo Valorant desativado. Saindo...')
            # sinalizar fim do "stream" local para reativar o input do cliente
            socketio.emit('stream_end')
            return
        socketio.emit('message', 'Saindo...')
        socketio.emit('stream_end')
        return

    # iniciar tarefa em background para não bloquear o event loop
    socketio.start_background_task(stream_to_clients, text)


if __name__ == '__main__':
    # porta 5000 por padrão
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
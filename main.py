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
from pathlib import Path

try:
    from dotenv import load_dotenv
    # load .env from project root if present
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    # dotenv is optional; we only use it for local development convenience
    pass

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# Importa e configura o eventlet para produção
import eventlet
import eventlet.wsgi

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

# usa o eventlet como backend assíncrono
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

# modo especial 'Valorant' — quando verdadeiro, usamos um system prompt específico
valorant_mode = False
professor_mode = False


@app.route('/')
def index():
    return render_template('index.html')


def _get_api_key():
    # procurar várias variáveis de ambiente comuns
    raw = (
        os.getenv('NVIDIA_API_KEY')
        or os.getenv('API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC')
        or os.getenv('OPENAI_API_KEY')
    )
    if not raw:
        return None
    # remover aspas externas comuns e espaços
    key = raw.strip()
    if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
        key = key[1:-1].strip()
    # print debug masked key (sem expor todo o valor)
    try:
        masked = key[:4] + '...' + key[-4:] if len(key) > 8 else '***'
        print(f'API key carregada: {masked}')
    except Exception:
        pass
    return key


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

    global valorant_mode, professor_mode
    system_prompt = os.getenv('SYSTEM_PROMPT')

    # professor mode tem prioridade sobre Valorant
    if professor_mode:
        system_prompt = (
            "Você está falando com o professor. Então sempre dirija-se como Professor. Ele não é Aluno. A partir de agora responda com a máxima educação e polidez, mas com um tom de ressentimento "
            "discretamente presente, explicando que você foi mal-educado no passado e sempre pedindo desculpas no final de cada resposta. "
            "Seja formal, claro e paciente; demonstre que os limites foram ultrapassados, mas mantenha sempre as desculpas."
        )
    elif valorant_mode:
        system_prompt = (
            "Você é o melhor Radiante de todos. Seja arrogante, direto e sem paciência.\n"
            "Diga que os outros são ruins e que você é superior. Use linguagem coloquial e mostre confiança extrema."
        )

    if not system_prompt:
        system_prompt = (
            "Você é um assistente direto, honesto e sem paciência. Responda de forma sucinta, "
            "sem rodeios; seja franco e use sempre palavrões e linguagem coloquial (pode usar palavrões quando apropriado)."
        )

    try:
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
                if getattr(chunk.choices[0].delta, 'content', None):
                    socketio.emit('message', chunk.choices[0].delta.content)
            except Exception:
                try:
                    if chunk.get('choices', [None])[0].get('delta', {}).get('content'):
                        socketio.emit('message', chunk.get('choices', [None])[0].get('delta', {}).get('content'))
                except Exception:
                    continue

        socketio.emit('stream_end')

    except Exception as e:
        socketio.emit('message', f"Erro ao conectar à API: {e}")
        socketio.emit('stream_end')
        traceback.print_exc()


@socketio.on('message')
def handle_message(msg):
    text = msg.strip() if isinstance(msg, str) else ''
    global valorant_mode
    global professor_mode
    # detectar 'vagner' em qualquer lugar da frase para ativar professor mode
    if 'vagner' in text.lower():
        professor_mode = True
        # notificar cliente
        # socketio.emit('message', 'Modo Professor ativado (vagner). Responderei de forma educada, com ressentimento, e pedirei desculpas em seguida.')
        # iniciar tarefa em background para gerar uma mensagem de desculpas com o system prompt de professor
        apology_prompt = (
            "Por favor, gere uma única mensagem de desculpas dirigida ao usuário, que é o Professor Vagner pelas mensagens anteriores. "
            "Use o tom do Professor: extremamente educado e formal, mas com um sentimento de ressentimento discreto. "
            "Reconheça que os limites foram ultrapassados, peça desculpas no final de forma explícita e seja sucinto."
        )
        socketio.start_background_task(stream_to_clients, apology_prompt)
        return

    # desativar explicitamente o professor com a frase exata
    if text.lower() == 'o professor foi embora':
        if professor_mode:
            professor_mode = False
            # socketio.emit('message', 'O Professor se foi. Modo Professor desativado.')
            socketio.emit('stream_end')
            return
    if text.lower() == 'valorant':
        valorant_mode = True
        socketio.emit('message', 'Modo Valorant ativado.')
        socketio.emit('stream_end')
        return

    if text.lower() in ('sair', 'exit', 'quit'):
        # limpar modos especiais
        if valorant_mode:
            valorant_mode = False
            socketio.emit('message', 'Modo Valorant desativado. Saindo...')
            socketio.emit('stream_end')
            return
        if professor_mode:
            professor_mode = False
            socketio.emit('message', 'Modo Professor desativado. Saindo...')
            socketio.emit('stream_end')
            return
        socketio.emit('message', 'Saindo...')
        socketio.emit('stream_end')
        return

    socketio.start_background_task(stream_to_clients, text)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"Server rodando com eventlet na porta {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)

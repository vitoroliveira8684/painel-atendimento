from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS
import requests
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# CHAVE DE SEGURANÇA (Necessária para sessões de login)
app.secret_key = 'sua_chave_secreta_super_segura' 
CORS(app)

API_BASE_URL = "https://api.gptmaker.ai/v2"
TOKEN = os.getenv("GPTMAKER_TOKEN")
WORKSPACE_ID = os.getenv("GPTMAKER_WORKSPACE_ID")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# --- CONFIGURAÇÃO DE USUÁRIOS ---
USUARIOS = {
    "admin": {"senha": "123", "role": "gestor"},
    "operador": {"senha": "123", "role": "operador"}
}

def formatar_data(timestamp):
    if not timestamp: return datetime.datetime.now().isoformat()
    try:
        dt = datetime.datetime.fromtimestamp(timestamp / 1000)
        return dt.isoformat()
    except: return str(timestamp)

# --- ROTAS DE ACESSO E LOGIN ---

@app.route('/')
def root():
    # Se já estiver logado, redireciona para a home certa
    if 'usuario' in session:
        if session['role'] == 'gestor':
            return redirect(url_for('gestao'))
        else:
            return redirect(url_for('comercial'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        user = request.form.get('usuario')
        pwd = request.form.get('senha')
        
        if user in USUARIOS and USUARIOS[user]['senha'] == pwd:
            session['usuario'] = user
            session['role'] = USUARIOS[user]['role']
            
            # Redirecionamento baseado no cargo
            if session['role'] == 'gestor':
                return redirect(url_for('gestao'))
            else:
                return redirect(url_for('comercial'))
        else:
            erro = "Usuário ou senha incorretos."

    return render_template('login.html', erro=erro)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ROTAS DAS PÁGINAS (PROTEGIDAS) ---

@app.route('/gestao')
def gestao():
    if 'usuario' not in session or session['role'] != 'gestor':
        return redirect(url_for('login'))
    return render_template('index.html') 

@app.route('/comercial')
def comercial():
    if 'usuario' not in session: # Operador e Gestor podem ver, ou restrinja se quiser
        return redirect(url_for('login'))
    return render_template('comercial.html') 

# --- API (O resto continua igual) ---

@app.route('/api/conversas', methods=['GET'])
def listar_conversas():
    if not WORKSPACE_ID: return jsonify({'erro': 'WORKSPACE_ID off'}), 500
    url = f"{API_BASE_URL}/workspace/{WORKSPACE_ID}/chats?pageSize=20"
    try:
        response = requests.get(url, headers=HEADERS)
        items = response.json().get('data', []) if isinstance(response.json(), dict) else response.json()
        conversas_formatadas = []
        for chat in items:
            conversas_formatadas.append({
                'id': chat.get('id'),
                'humanTalk': chat.get('humanTalk', False),
                'resumo_mensagem': chat.get('conversation', 'Nova conversa'),
                'ultima_interacao': formatar_data(chat.get('time')),
                'cliente': {
                    'nome': chat.get('name', 'Cliente'),
                    'telefone': chat.get('whatsappPhone', '')
                }
            })
        return jsonify(conversas_formatadas)
    except Exception as e: return jsonify([]), 500

@app.route('/api/fila_humanos', methods=['GET'])
def listar_fila_humanos():
    if not WORKSPACE_ID: return jsonify([]), 500
    url = f"{API_BASE_URL}/workspace/{WORKSPACE_ID}/chats?pageSize=50"
    try:
        response = requests.get(url, headers=HEADERS)
        items = response.json().get('data', []) if isinstance(response.json(), dict) else response.json()
        fila = []
        for chat in items:
            if chat.get('humanTalk') is True:
                fila.append({
                    'id': chat.get('id'),
                    'resumo_mensagem': chat.get('conversation', 'Aguardando atendimento...'),
                    'ultima_interacao': formatar_data(chat.get('time')),
                    'cliente': {
                        'nome': chat.get('name', 'Cliente'),
                        'telefone': chat.get('whatsappPhone', '')
                    }
                })
        return jsonify(fila)
    except Exception as e: return jsonify([]), 500

@app.route('/api/mensagens/<chat_id>', methods=['GET'])
def obter_mensagens(chat_id):
    url = f"{API_BASE_URL}/chat/{chat_id}/messages?pageSize=50"
    try:
        response = requests.get(url, headers=HEADERS)
        lista_msgs = response.json()
        items_msg = lista_msgs.get('data', []) if isinstance(lista_msgs, dict) else lista_msgs
        mensagens_formatadas = []
        for msg in items_msg:
            role_api = msg.get('role', '').lower()
            origem = 'cliente' if role_api in ['user', 'contact', 'customer'] else 'agente'
            mensagens_formatadas.append({
                'texto': msg.get('text', ''),
                'origem': origem,
                'data_envio': formatar_data(msg.get('time'))
            })
        mensagens_formatadas.sort(key=lambda x: x['data_envio'])
        return jsonify(mensagens_formatadas)
    except: return jsonify([]), 500

@app.route('/api/enviar_resposta', methods=['POST'])
def enviar_resposta():
    data = request.get_json()
    url = f"{API_BASE_URL}/chat/{data.get('conversa_id')}/send-message"
    try:
        requests.post(url, json={"message": data.get('texto_resposta')}, headers=HEADERS)
        return jsonify({'status': 'ok'}), 200
    except: return jsonify({'error': 'erro'}), 500

@app.route('/api/finalizar_atendimento', methods=['POST'])
def finalizar_atendimento():
    # Rota para tirar da lista e Devolver pro Bot
    data = request.get_json()
    chat_id = data.get('conversa_id')
    
    if not chat_id:
        return jsonify({'erro': 'ID invalido'}), 400

    # URL OFICIAL da documentação para Encerrar Atendimento Humano
    url = f"{API_BASE_URL}/chat/{chat_id}/stop-human" 
    
    try:
        # A documentação pede um PUT
        response = requests.put(url, headers=HEADERS)
        
        if response.status_code == 200:
            # Sucesso: A flag humanTalk virou False lá no GPT Maker.
            # O filtro do listar_fila_humanos vai parar de pegar essa conversa automaticamente.
            return jsonify({'status': 'finalizado', 'msg': 'Controle devolvido ao bot'}), 200
        else:
            # Log de erro para debug se precisar
            print(f"Erro GPTMaker: {response.status_code} - {response.text}")
            return jsonify({'erro': 'Falha ao finalizar na API'}), response.status_code

    except Exception as e:
        print(f"Erro Python: {e}")
        return jsonify({'erro': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
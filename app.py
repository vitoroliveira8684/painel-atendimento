import os
import datetime
import logging
from functools import wraps
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS
import requests
from dotenv import load_dotenv

# --- CONFIGURAÇÃO INICIAL ---
load_dotenv()

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# SEGURANÇA: Carrega do .env ou usa fallback (APENAS para dev)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'chave_padrao_insegura_troque_no_render')

# --- CONFIGURAÇÃO GPT MAKER ---
TOKEN = os.getenv("GPTMAKER_TOKEN")
WORKSPACE_ID = os.getenv("GPTMAKER_WORKSPACE_ID")
API_BASE_URL = "https://api.gptmaker.ai/v2"

if not TOKEN or not WORKSPACE_ID:
    logger.critical("ERRO CRÍTICO: Variáveis GPTMAKER_TOKEN ou WORKSPACE_ID não configuradas.")

# OTIMIZAÇÃO: Reuse de Sessão TCP (Connection Pooling)
# Isso acelera as requisições pois não precisa refazer o handshake SSL a cada chamada
gpt_session = requests.Session()
gpt_session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
})

# --- USUÁRIOS (Idealmente mover para Banco de Dados no futuro) ---
# Você pode definir senhas via variáveis de ambiente também para maior segurança
SENHA_ADMIN = os.getenv("SENHA_ADMIN", "123")
SENHA_OPERADOR = os.getenv("SENHA_OPERADOR", "123")

USUARIOS = {
    "admin": {"senha": SENHA_ADMIN, "role": "gestor"},
    "operador": {"senha": SENHA_OPERADOR, "role": "operador"}
}

# --- HELPERS ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def formatar_data(timestamp):
    if not timestamp: 
        return datetime.datetime.now().isoformat()
    try:
        # GPTMaker costuma enviar timestamp em milissegundos
        dt = datetime.datetime.fromtimestamp(timestamp / 1000)
        return dt.isoformat()
    except Exception:
        return str(timestamp)

# --- ROTAS DE ACESSO ---

@app.route('/')
def root():
    if 'usuario' in session:
        return redirect(url_for('gestao' if session['role'] == 'gestor' else 'comercial'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    erro = None
    if request.method == 'POST':
        user = request.form.get('usuario')
        pwd = request.form.get('senha')
        
        if user in USUARIOS and USUARIOS[user]['senha'] == pwd:
            session.permanent = True  # Sessão persiste por padrão (31 dias no Flask)
            session['usuario'] = user
            session['role'] = USUARIOS[user]['role']
            logger.info(f"Login efetuado: {user}")
            
            if session['role'] == 'gestor':
                return redirect(url_for('gestao'))
            else:
                return redirect(url_for('comercial'))
        else:
            erro = "Credenciais inválidas."
            logger.warning(f"Tentativa de login falha: {user}")

    return render_template('login.html', erro=erro)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- PÁGINAS PROTEGIDAS ---

@app.route('/gestao')
@login_required
def gestao():
    if session['role'] != 'gestor':
        return redirect(url_for('comercial'))
    return render_template('index.html') 

@app.route('/comercial')
@login_required
def comercial():
    return render_template('comercial.html') 

# --- API ENDPOINTS (OTIMIZADOS) ---

@app.route('/api/conversas', methods=['GET'])
@login_required
def listar_conversas():
    # Rota para Gestor (vê tudo)
    url = f"{API_BASE_URL}/workspace/{WORKSPACE_ID}/chats?pageSize=20"
    try:
        response = gpt_session.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        items = data.get('data', []) if isinstance(data, dict) else data
        
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
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao listar conversas: {e}")
        return jsonify([]), 502 # Bad Gateway

@app.route('/api/fila_humanos', methods=['GET'])
@login_required
def listar_fila_humanos():
    # Rota para Operador (vê apenas humanTalk=true)
    url = f"{API_BASE_URL}/workspace/{WORKSPACE_ID}/chats?pageSize=50"
    try:
        response = gpt_session.get(url, timeout=10)
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
    except Exception as e:
        logger.error(f"Erro fila humanos: {e}")
        return jsonify([]), 500

@app.route('/api/mensagens/<chat_id>', methods=['GET'])
@login_required
def obter_mensagens(chat_id):
    url = f"{API_BASE_URL}/chat/{chat_id}/messages?pageSize=50"
    try:
        response = gpt_session.get(url, timeout=8)
        lista_msgs = response.json()
        items_msg = lista_msgs.get('data', []) if isinstance(lista_msgs, dict) else lista_msgs
        
        mensagens_formatadas = []
        for msg in items_msg:
            role_api = msg.get('role', '').lower()
            # Normaliza quem enviou
            origem = 'cliente' if role_api in ['user', 'contact', 'customer'] else 'agente'
            mensagens_formatadas.append({
                'texto': msg.get('text', ''),
                'origem': origem,
                'data_envio': formatar_data(msg.get('time'))
            })
        
        mensagens_formatadas.sort(key=lambda x: x['data_envio'])
        return jsonify(mensagens_formatadas)
    except Exception as e:
        return jsonify([]), 500

@app.route('/api/enviar_resposta', methods=['POST'])
@login_required
def enviar_resposta():
    data = request.get_json()
    chat_id = data.get('conversa_id')
    texto = data.get('texto_resposta')
    
    if not chat_id or not texto:
        return jsonify({'erro': 'Dados incompletos'}), 400

    url = f"{API_BASE_URL}/chat/{chat_id}/send-message"
    try:
        # Payload padrão do GPT Maker
        payload = {"message": texto}
        res = gpt_session.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Erro envio mensagem: {e}")
        return jsonify({'error': 'Falha ao enviar'}), 500

@app.route('/api/finalizar_atendimento', methods=['POST'])
@login_required
def finalizar_atendimento():
    data = request.get_json()
    chat_id = data.get('conversa_id')
    
    if not chat_id:
        return jsonify({'erro': 'ID inválido'}), 400

    # Rota para devolver ao bot
    url = f"{API_BASE_URL}/chat/{chat_id}/stop-human" 
    
    try:
        response = gpt_session.put(url, timeout=10)
        if response.status_code == 200:
            return jsonify({'status': 'finalizado', 'msg': 'Controle devolvido ao bot'}), 200
        else:
            return jsonify({'erro': 'Falha na API externa'}), response.status_code
    except Exception as e:
        logger.error(f"Erro finalizar: {e}")
        return jsonify({'erro': str(e)}), 500

if __name__ == '__main__':
    # Em produção (Render), o Gunicorn quem chama o app, mas isso serve para testes locais
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
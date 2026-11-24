import os
import json
import base64
import csv
import io
from functools import wraps
from datetime import datetime, timezone, timedelta

import paho.mqtt.client as mqtt
import resend
from apscheduler.schedulers.background import BackgroundScheduler
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, send_from_directory, Response
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash


# ********************* CARREGAMENTO DO .ENV *******************************
def carregar_env(caminho=".env"):
    if not os.path.exists(caminho):
        return
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip())


carregar_env()

FUSO_SP = timezone(timedelta(hours=-3))


def agora_sp():
    return datetime.now(FUSO_SP).replace(tzinfo=None)


# ********************* GERENCIAMENTO CHAVES VAPID & PUSH ******************
VAPID_FILE = "vapid_keys.json"
SUBS_FILE = "push_subscriptions.json"


def obter_ou_gerar_vapid():
    if os.path.exists(VAPID_FILE):
        with open(VAPID_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return dados["public_key"], dados["private_key"]

    private_key = ec.generate_private_key(ec.SECP256R1())
    priv_val = private_key.private_numbers().private_value.to_bytes(32, "big")
    priv_b64 = base64.urlsafe_b64encode(priv_val).rstrip(b"=").decode("utf-8")

    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("utf-8")

    with open(VAPID_FILE, "w", encoding="utf-8") as f:
        json.dump({"public_key": pub_b64, "private_key": priv_b64}, f, indent=2)

    return pub_b64, priv_b64


VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY = obter_ou_gerar_vapid()


def carregar_inscritos():
    if not os.path.exists(SUBS_FILE):
        return []
    try:
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def salvar_inscritos(lista):
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(lista, f, indent=2)


inscritos = carregar_inscritos()


# ********************* FLASK & BANCO MYSQL ********************************
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "datacenter_noc_secret_2026")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URI",
    "mysql+pymysql://root:@localhost:3306/monitoramento_ambiental"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

mybd = SQLAlchemy(app)
resend.api_key = os.environ.get("RESEND_API_KEY", "")


class Usuario(mybd.Model):
    __tablename__ = "usuarios"

    id = mybd.Column(mybd.Integer, primary_key=True, autoincrement=True)
    nome = mybd.Column(mybd.String(150), nullable=False)
    email = mybd.Column(mybd.String(150), unique=True)
    senha_hash = mybd.Column(mybd.String(150), nullable=False)
    data_cadastro = mybd.Column(mybd.DateTime, nullable=False, default=agora_sp)


class Registro(mybd.Model):
    __tablename__ = "monitoramento_ambiental"

    id = mybd.Column(mybd.Integer, primary_key=True, autoincrement=True)
    temperature = mybd.Column(mybd.Float)
    pressure = mybd.Column(mybd.Float)
    altitude = mybd.Column(mybd.Float)
    humidity = mybd.Column(mybd.Float)
    CO2 = mybd.Column(mybd.Float)
    particula_1 = mybd.Column(mybd.Float)
    particula_2 = mybd.Column(mybd.Float)
    registro_tempo = mybd.Column(mybd.DateTime, nullable=False)


class HistoricoAlerta(mybd.Model):
    __tablename__ = "historico_alertas"

    id = mybd.Column(mybd.Integer, primary_key=True, autoincrement=True)
    id_leitura = mybd.Column(mybd.Integer)
    tipo_evento = mybd.Column(mybd.String(150), nullable=False)
    mensagem_enviada = mybd.Column(mybd.Text, nullable=False)
    status_envio = mybd.Column(mybd.String(150), nullable=False)
    data_alerta = mybd.Column(mybd.DateTime, nullable=False, default=agora_sp)


# ********************* PARÂMETROS DATA CENTER (ASHRAE / ANVISA) ***********
LIMITES = {
    "temperature": {"ideal_min": 18.0, "ideal_max": 25.0, "crit_max": 27.0, "crit_min": 15.0},
    "humidity": {"ideal_min": 40.0, "ideal_max": 60.0, "crit_max": 65.0, "crit_min": 35.0},
    "CO2": {"ideal_max": 800.0, "crit_max": 1000.0},
    "particula_1": {"ideal_max": 15.0, "crit_max": 35.0},
    "particula_2": {"ideal_max": 50.0, "crit_max": 100.0},
}

ultimo_disparo_evento = {}
COOLDOWN_MINUTOS = 15


def avaliar_status_sensores(reg):
    if not reg:
        return {}

    def status_faixa(val, ideal_min, ideal_max, crit_min, crit_max):
        if val is None:
            return "sem_dados"
        if val > crit_max or val < crit_min:
            return "critico"
        if val > ideal_max or val < ideal_min:
            return "atencao"
        return "normal"

    def status_teto(val, ideal_max, crit_max):
        if val is None:
            return "sem_dados"
        if val > crit_max:
            return "critico"
        if val > ideal_max:
            return "atencao"
        return "normal"

    return {
        "temperature": status_faixa(reg.temperature, 18.0, 25.0, 15.0, 27.0),
        "humidity": status_faixa(reg.humidity, 40.0, 60.0, 35.0, 65.0),
        "CO2": status_teto(reg.CO2, 800.0, 1000.0),
        "particula_1": status_teto(reg.particula_1, 15.0, 35.0),
        "particula_2": status_teto(reg.particula_2, 50.0, 100.0),
        "pressure": "normal" if reg.pressure is not None else "sem_dados",
    }


# ********************* NOTIFICAÇÕES (WEB PUSH + RESEND) *******************
def enviar_push(titulo, corpo):
    if not inscritos:
        return "Sem navegadores inscritos"

    enviados = 0
    invalidos = []
    for sub in inscritos:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": titulo, "body": corpo}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": "mailto:noc-datacenter@senai134.edu.br"},
            )
            enviados += 1
        except WebPushException as ex:
            print(f"[Push] Erro de inscrição: {ex}")
            if ex.response is not None and ex.response.status_code in (404, 410):
                invalidos.append(sub)
        except Exception as e:
            print(f"[Push] Falha: {e}")

    if invalidos:
        for inv in invalidos:
            if inv in inscritos:
                inscritos.remove(inv)
        salvar_inscritos(inscritos)

    return f"Push OK ({enviados})" if enviados > 0 else "Push falhou"


def enviar_email_alerta(assunto, titulo, detalhes_html):
    api_key = os.environ.get("RESEND_API_KEY", "")
    destino = os.environ.get("EMAIL_DESTINO", "")

    if not api_key or api_key.startswith("re_sua_chave") or not destino:
        return "E-mail pendente (configurar .env)"

    resend.api_key = api_key
    html = f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; background:#090D16; color:#F8FAFC; padding:24px; border-radius:8px; max-width:560px;">
        <div style="border-left:4px solid #EF4444; padding-left:14px; margin-bottom:18px;">
            <span style="color:#EF4444; font-size:12px; font-weight:700; letter-spacing:1px;">ALERTA CRÍTICO • DATA CENTER NOC</span>
            <h2 style="margin:6px 0 0 0; font-size:20px; color:#FFFFFF;">{titulo}</h2>
        </div>
        <div style="background:#111827; border:1px solid #1F2937; padding:16px; border-radius:6px; font-size:14px; line-height:1.6; color:#CBD5E1;">
            {detalhes_html}
        </div>
        <p style="font-size:12px; color:#64748B; margin-top:16px;">
            Sistema de Monitoramento Ambiental • SENAI 134 • Norma ASHRAE TC 9.9
        </p>
    </div>
    """
    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": destino,
            "subject": assunto,
            "html": html,
        })
        return "E-mail Enviado"
    except Exception as e:
        print(f"[Resend] Erro ao enviar alerta: {e}")
        return f"Erro E-mail: {str(e)[:60]}"


def processar_alertas(registro, ignorar_cooldown=False):
    agora = agora_sp()
    eventos = []

    if registro.temperature is not None and registro.temperature > LIMITES["temperature"]["crit_max"]:
        eventos.append((
            "Temperatura Crítica (> 27°C)",
            f"Temperatura atingiu {registro.temperature:.1f} °C no Rack Principal (limite ASHRAE: 27.0 °C).",
            f"<strong>Variável:</strong> Temperatura<br>"
            f"<strong>Leitura Atual:</strong> <span style='color:#EF4444;font-weight:bold;'>{registro.temperature:.1f} °C</span><br>"
            f"<strong>Limite Crítico:</strong> 27.0 °C (ASHRAE TC 9.9)<br>"
            f"<strong>Umidade / CO₂:</strong> {registro.humidity or 0:.1f}% | {registro.CO2 or 0:.0f} ppm<br>"
            f"<strong>Horário:</strong> {registro.registro_tempo.strftime('%d/%m/%Y %H:%M:%S')}"
        ))

    if registro.humidity is not None and (registro.humidity > 65.0 or registro.humidity < 35.0):
        cond = "Condensação (>65%)" if registro.humidity > 65.0 else "Risco ESD (<35%)"
        eventos.append((
            f"Umidade Crítica - {cond}",
            f"Umidade relativa em {registro.humidity:.1f}% (faixa segura: 40% a 60%).",
            f"<strong>Variável:</strong> Umidade Relativa<br>"
            f"<strong>Leitura Atual:</strong> <span style='color:#EF4444;font-weight:bold;'>{registro.humidity:.1f}%</span><br>"
            f"<strong>Diagnóstico:</strong> {cond}<br>"
            f"<strong>Horário:</strong> {registro.registro_tempo.strftime('%d/%m/%Y %H:%M:%S')}"
        ))

    if registro.CO2 is not None and registro.CO2 > 1000.0:
        eventos.append((
            "CO₂ Crítico (> 1000 ppm)",
            f"Concentração de CO₂ em {registro.CO2:.0f} ppm na sala restrita.",
            f"<strong>Variável:</strong> Dióxido de Carbono (CO₂)<br>"
            f"<strong>Leitura Atual:</strong> <span style='color:#EF4444;font-weight:bold;'>{registro.CO2:.0f} ppm</span><br>"
            f"<strong>Horário:</strong> {registro.registro_tempo.strftime('%d/%m/%Y %H:%M:%S')}"
        ))

    if registro.particula_1 is not None and registro.particula_1 > 35.0:
        eventos.append((
            "Particula do PM2.5 Crítico",
            f"Material particula do fino (PM2.5) atingiu {registro.particula_1:.1f} µg/m³.",
            f"<strong>Variável:</strong> Particula do PM2.5<br>"
            f"<strong>Leitura Atual:</strong> <span style='color:#EF4444;font-weight:bold;'>{registro.particula_1:.1f} µg/m³</span><br>"
            f"<strong>Horário:</strong> {registro.registro_tempo.strftime('%d/%m/%Y %H:%M:%S')}"
        ))

    alertas_gerados = []
    for tipo_evento, msg_curta, msg_html in eventos:
        ultimo = ultimo_disparo_evento.get(tipo_evento)
        if not ignorar_cooldown and ultimo and (agora - ultimo) < timedelta(minutes=COOLDOWN_MINUTOS):
            continue

        ultimo_disparo_evento[tipo_evento] = agora

        status_push = enviar_push(f"🚨 {tipo_evento}", msg_curta)
        status_email = enviar_email_alerta(f"[ALERTA NOC] {tipo_evento}", tipo_evento, msg_html)
        status_final = f"{status_push} | {status_email}"

        novo_alerta = HistoricoAlerta(
            id_leitura=registro.id,
            tipo_evento=tipo_evento,
            mensagem_enviada=msg_curta,
            status_envio=status_final,
            data_alerta=agora
        )
        mybd.session.add(novo_alerta)
        alertas_gerados.append(novo_alerta)

    if alertas_gerados:
        mybd.session.commit()

    return alertas_gerados


# ********************* RELATÓRIO DIÁRIO (21:30) ***************************
def enviar_resumo_email():
    with app.app_context():
        try:
            agora = agora_sp()
            desde = agora - timedelta(hours=24)

            stats = mybd.session.query(
                func.count(Registro.id),
                func.avg(Registro.temperature),
                func.min(Registro.temperature),
                func.max(Registro.temperature),
                func.avg(Registro.humidity),
                func.avg(Registro.CO2),
                func.avg(Registro.particula_1)
            ).filter(Registro.registro_tempo >= desde).one()

            total_leituras, t_avg, t_min, t_max, u_avg, co2_avg, pm_avg = stats
            total_alertas = mybd.session.query(func.count(HistoricoAlerta.id)).filter(
                HistoricoAlerta.data_alerta >= desde
            ).scalar()

            def fmt(v, suf=""):
                return f"{v:.1f}{suf}" if v is not None else "Sem dados"

            html = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; background:#090D16; color:#F8FAFC; padding:24px; border-radius:8px; max-width:600px;">
                <div style="border-left:4px solid #10B981; padding-left:14px; margin-bottom:20px;">
                    <span style="color:#10B981; font-size:12px; font-weight:700; letter-spacing:1px;">RELATÓRIO DIÁRIO • DATA CENTER NOC</span>
                    <h2 style="margin:6px 0 0 0; font-size:20px; color:#FFFFFF;">Resumo Operacional (Últimas 24h)</h2>
                    <p style="margin:4px 0 0 0; font-size:12px; color:#94A3B8;">Fechamento automático às {agora.strftime('%d/%m/%Y %H:%M')}</p>
                </div>

                <table style="width:100%; border-collapse:collapse; background:#111827; border:1px solid #1F2937; font-size:14px;">
                    <tr style="border-bottom:1px solid #1F2937;">
                        <td style="padding:12px; color:#94A3B8;">Total de Leituras Coletadas</td>
                        <td style="padding:12px; text-align:right; font-weight:bold; color:#FFFFFF;">{total_leituras or 0} pacotes</td>
                    </tr>
                    <tr style="border-bottom:1px solid #1F2937;">
                        <td style="padding:12px; color:#94A3B8;">Temperatura (Média / Mín / Máx)</td>
                        <td style="padding:12px; text-align:right; font-weight:bold; color:#FFFFFF;">{fmt(t_avg, ' °C')} | {fmt(t_min, ' °C')} | {fmt(t_max, ' °C')}</td>
                    </tr>
                    <tr style="border-bottom:1px solid #1F2937;">
                        <td style="padding:12px; color:#94A3B8;">Umidade Relativa Média</td>
                        <td style="padding:12px; text-align:right; font-weight:bold; color:#FFFFFF;">{fmt(u_avg, '%')}</td>
                    </tr>
                    <tr style="border-bottom:1px solid #1F2937;">
                        <td style="padding:12px; color:#94A3B8;">CO₂ Médio / PM2.5 Médio</td>
                        <td style="padding:12px; text-align:right; font-weight:bold; color:#FFFFFF;">{fmt(co2_avg, ' ppm')} | {fmt(pm_avg, ' µg/m³')}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px; color:#94A3B8;">Incidentes Críticos Registrados</td>
                        <td style="padding:12px; text-align:right; font-weight:bold; color:{'#EF4444' if total_alertas else '#10B981'};">{total_alertas or 0} alertas</td>
                    </tr>
                </table>
            </div>
            """

            api_key = os.environ.get("RESEND_API_KEY", "")
            destino = os.environ.get("EMAIL_DESTINO", "")
            if not api_key or api_key.startswith("re_sua_chave") or not destino:
                print("[Resumo 21:30] RESEND_API_KEY ou EMAIL_DESTINO não configurados no .env")
                return False

            resend.api_key = api_key
            resend.Emails.send({
                "from": "onboarding@resend.dev",
                "to": destino,
                "subject": f"📊 Relatório Diário Data Center — {agora.strftime('%d/%m/%Y')}",
                "html": html,
            })
            print("[Resumo 21:30] E-mail diário enviado com sucesso!")
            return True
        except Exception as e:
            print(f"[Resumo 21:30] Erro ao enviar e-mail: {e}")
            return False


def start_agendador():
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(enviar_resumo_email, "cron", hour=21, minute=30)
    scheduler.start()


# ********************* AUTENTICAÇÃO & PÁGINAS *****************************
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "usuario_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"erro": "Não autenticado"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    if "usuario_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        usuario = Usuario.query.filter_by(email=email).first()
        if usuario and check_password_hash(usuario.senha_hash, senha):
            session["usuario_id"] = usuario.id
            session["usuario_nome"] = usuario.nome
            session["usuario_email"] = usuario.email
            return redirect(url_for("dashboard"))

        return render_template("login.html", erro="E-mail ou senha inválidos.", aba="login")

    return render_template("login.html", aba="login")


@app.route("/cadastro", methods=["POST"])
def cadastro():
    nome = request.form.get("nome", "").strip()
    email = request.form.get("email", "").strip().lower()
    senha = request.form.get("senha", "")

    if not nome or not email or not senha:
        return render_template("login.html", erro="Preencha todos os campos.", aba="cadastro")

    if Usuario.query.filter_by(email=email).first():
        return render_template("login.html", erro="Este e-mail já está cadastrado.", aba="cadastro")

    novo_usuario = Usuario(
        nome=nome,
        email=email,
        senha_hash=generate_password_hash(senha, method="pbkdf2:sha256"),
        data_cadastro=agora_sp()
    )
    mybd.session.add(novo_usuario)
    mybd.session.commit()

    session["usuario_id"] = novo_usuario.id
    session["usuario_nome"] = novo_usuario.nome
    session["usuario_email"] = novo_usuario.email
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        usuario_nome=session.get("usuario_nome", "Operador"),
        mqtt_topic=os.environ.get("MQTT_TOPIC", "projeto_integrado/SENAI134/Cienciadedados/grupoX")
    )


# ********************* ENDPOINTS REST DA API ******************************
@app.route("/api/atual")
@login_required
def api_atual():
    ultimo = Registro.query.order_by(Registro.id.desc()).first()
    if not ultimo:
        return jsonify({"online": False, "dados": None, "status": {}})

    agora = agora_sp()
    segundos_atras = (agora - ultimo.registro_tempo).total_seconds()
    online = abs(segundos_atras) <= 120  # Online se recebeu pacote nos últimos 2 min

    return jsonify({
        "online": online,
        "segundos_desde_ultima": max(0, int(segundos_atras)),
        "ultima_atualizacao": ultimo.registro_tempo.strftime("%d/%m/%Y %H:%M:%S"),
        "dados": {
            "id": ultimo.id,
            "temperature": ultimo.temperature,
            "pressure": ultimo.pressure,
            "altitude": ultimo.altitude,
            "humidity": ultimo.humidity,
            "CO2": ultimo.CO2,
            "particula_1": ultimo.particula_1,
            "particula_2": ultimo.particula_2,
        },
        "status": avaliar_status_sensores(ultimo),
        "limites": LIMITES
    })


@app.route("/api/historico")
@login_required
def api_historico():
    periodo = request.args.get("periodo", "24h")
    inicio_str = request.args.get("inicio")
    fim_str = request.args.get("fim")

    agora = agora_sp()
    query = Registro.query

    if inicio_str and fim_str:
        try:
            dt_inicio = datetime.fromisoformat(inicio_str)
            dt_fim = datetime.fromisoformat(fim_str)
            query = query.filter(Registro.registro_tempo >= dt_inicio, Registro.registro_tempo <= dt_fim)
        except ValueError:
            pass
    else:
        mapa_horas = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
        horas = mapa_horas.get(periodo, 24)
        desde = agora - timedelta(hours=horas)
        query = query.filter(Registro.registro_tempo >= desde)

    registros = query.order_by(Registro.registro_tempo.asc()).limit(500).all()

    return jsonify({
        "total": len(registros),
        "registros": [
            {
                "id": r.id,
                "temperature": r.temperature,
                "pressure": r.pressure,
                "altitude": r.altitude,
                "humidity": r.humidity,
                "CO2": r.CO2,
                "particula_1": r.particula_1,
                "particula_2": r.particula_2,
                "registro_tempo": r.registro_tempo.strftime("%Y-%m-%d %H:%M:%S"),
                "label_hora": r.registro_tempo.strftime("%d/%m %H:%M:%S")
            }
            for r in registros
        ]
    })


@app.route("/api/alertas")
@login_required
def api_alertas():
    alertas = HistoricoAlerta.query.order_by(HistoricoAlerta.id.desc()).limit(30).all()
    return jsonify({
        "alertas": [
            {
                "id": a.id,
                "id_leitura": a.id_leitura,
                "tipo_evento": a.tipo_evento,
                "mensagem_enviada": a.mensagem_enviada,
                "status_envio": a.status_envio,
                "data_alerta": a.data_alerta.strftime("%d/%m/%Y %H:%M:%S")
            }
            for a in alertas
        ]
    })


@app.route("/api/simular-alerta", methods=["POST"])
@login_required
def api_simular_alerta():
    """Insere uma leitura crítica simulada (>27°C) para demonstrar pop-up + e-mail + log."""
    leitura_simulada = Registro(
        temperature=28.6,
        pressure=1012.4,
        altitude=760.0,
        humidity=48.5,
        CO2=620.0,
        particula_1=11.2,
        particula_2=22.0,
        registro_tempo=agora_sp()
    )
    mybd.session.add(leitura_simulada)
    mybd.session.commit()

    alertas = processar_alertas(leitura_simulada, ignorar_cooldown=True)
    return jsonify({
        "status": "ok",
        "leitura_id": leitura_simulada.id,
        "alertas_disparados": len(alertas),
        "detalhe": alertas[0].status_envio if alertas else "Nenhum"
    })


@app.route("/api/teste-resumo-diario", methods=["POST"])
@login_required
def api_teste_resumo_diario():
    enviado = enviar_resumo_email()
    if enviado:
        return jsonify({"status": "ok", "mensagem": "Relatório diário enviado para o e-mail configurado!"})
    return jsonify({"status": "aviso", "mensagem": "Verifique RESEND_API_KEY e EMAIL_DESTINO no arquivo .env."}), 400


@app.route("/api/exportar-csv")
@login_required
def exportar_csv():
    registros = Registro.query.order_by(Registro.registro_tempo.desc()).limit(2000).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["id", "temperature", "pressure", "altitude", "humidity", "CO2", "particula_1", "particula_2", "registro_tempo"])

    for r in registros:
        writer.writerow([
            r.id, r.temperature, r.pressure, r.altitude,
            r.humidity, r.CO2, r.particula_1, r.particula_2,
            r.registro_tempo.strftime("%Y-%m-%d %H:%M:%S")
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=telemetria_datacenter.csv"}
    )


# ********************* ROTAS WEB PUSH & SERVICE WORKER ********************
@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/api/push/chave")
@login_required
def push_chave():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    sub = request.get_json()
    if sub and sub not in inscritos:
        inscritos.append(sub)
        salvar_inscritos(inscritos)
    return jsonify({"status": "ok", "total_inscritos": len(inscritos)})


# ********************* MQTT ***********************************************
mqtt_data = {}


def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))
    if rc == 0:
        topico = os.environ.get("MQTT_TOPIC", "projeto_integrado/SENAI134/Cienciadedados/grupoX")
        client.subscribe(topico)
        print(f"Inscrito no tópico: {topico}")


def on_message(client, userdata, msg):
    global mqtt_data
    try:
        payload = msg.payload.decode("utf-8")
        mqtt_data = json.loads(payload)
        print(f"Received message: {mqtt_data}")
    except Exception as e:
        print(f"Erro ao decodificar JSON MQTT: {e}")
        return

    with app.app_context():
        try:
            temperatura = mqtt_data.get("temperature")
            pressao = mqtt_data.get("pressure")
            altitude = mqtt_data.get("altitude")
            umidade = mqtt_data.get("humidity")
            co2 = mqtt_data.get("CO2")
            poeira1 = mqtt_data.get("particula1")
            poeira2 = mqtt_data.get("particula2")
            timestamp_unix = mqtt_data.get("timestamp")

            if timestamp_unix is None:
                print("Timestamp não encontrado no payload")
                return

            timestamp = datetime.fromtimestamp(int(timestamp_unix), tz=FUSO_SP).replace(tzinfo=None)

            new_data = Registro(
                temperature=temperatura,
                pressure=pressao,
                altitude=altitude,
                humidity=umidade,
                CO2=co2,
                particula_1=poeira1,
                particula_2=poeira2,
                registro_tempo=timestamp
            )

            mybd.session.add(new_data)
            mybd.session.commit()
            print(f"Dados inseridos no MySQL (ID {new_data.id})")

            # Avalia regras críticas (>27°C etc.) e dispara Web Push + E-mail
            processar_alertas(new_data)

        except Exception as e:
            print(f"Erro ao processar os dados do MQTT: {e}")
            mybd.session.rollback()


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


def start_mqtt():
    broker = os.environ.get("MQTT_BROKER", "test.mosquitto.org")
    porta = int(os.environ.get("MQTT_PORT", 1883))
    mqtt_client.connect(broker, porta, 60)
    mqtt_client.loop_start()


if __name__ == "__main__":
    with app.app_context():
        mybd.create_all()
        start_mqtt()
        start_agendador()

        port = int(os.environ.get("PORT", 5050))
        app.run(host="0.0.0.0", port=port)
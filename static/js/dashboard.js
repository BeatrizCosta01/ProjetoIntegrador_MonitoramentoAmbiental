let periodoAtual = '24h';
let chartTermico = null;
let chartAr = null;
let ultimoAlertaIdConhecido = null;

// ================= INICIALIZAÇÃO =================
document.addEventListener('DOMContentLoaded', () => {
    inicializarGraficos();
    atualizarTudo();
    verificarStatusPushInicial();

    // Atualização automática a cada 5 segundos
    setInterval(atualizarTudo, 5000);
});

async function atualizarTudo() {
    await Promise.all([
        carregarLeituraAtual(),
        carregarHistorico(),
        carregarAlertas()
    ]);
}

// ================= LEITURA ATUAL & KPIS =================
async function carregarLeituraAtual() {
    try {
        const resp = await fetch('/api/atual');
        if (resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        const json = await resp.json();

        const pill = document.getElementById('status-conexao');
        const pillTexto = document.getElementById('status-conexao-texto');

        if (!json.dados) {
            pill.className = 'status-pill offline';
            pillTexto.textContent = 'SEM DADOS NO BANCO';
            return;
        }

        document.getElementById('ultima-atualizacao').textContent = json.ultima_atualizacao;

        if (json.online) {
            pill.className = 'status-pill online';
            pillTexto.textContent = 'AO VIVO • MQTT';
        } else {
            pill.className = 'status-pill offline';
            pillTexto.textContent = `OFFLINE (${json.segundos_desde_ultima}s)`;
        }

        const d = json.dados;
        const st = json.status;

        atualizarCardKPI('temperature', d.temperature, 1, st.temperature);
        atualizarCardKPI('humidity', d.humidity, 1, st.humidity);
        atualizarCardKPI('CO2', d.CO2, 0, st.CO2);
        atualizarCardKPI('particula_1', d.particula_1, 1, st.particula_1);
        atualizarCardKPI('particula_2', d.particula_2, 1, st.particula_2);
        atualizarCardKPI('pressure', d.pressure, 1, st.pressure);

        if (d.altitude !== null && d.altitude !== undefined) {
            document.getElementById('val-altitude').textContent = Number(d.altitude).toFixed(0);
        }

        // Ponteiro visual na régua de Temperatura (15°C a 30°C)
        if (d.temperature !== null) {
            const pctTemp = Math.min(100, Math.max(0, ((d.temperature - 15) / 15) * 100));
            document.getElementById('pointer-temperature').style.left = `${pctTemp}%`;
        }

        // Ponteiro visual na régua de Umidade (30% a 70%)
        if (d.humidity !== null) {
            const pctHum = Math.min(100, Math.max(0, ((d.humidity - 30) / 40) * 100));
            document.getElementById('pointer-humidity').style.left = `${pctHum}%`;
        }

        // Banner Crítico Superior
        const banner = document.getElementById('banner-critico');
        if (st.temperature === 'critico' || st.humidity === 'critico' || st.CO2 === 'critico') {
            banner.classList.remove('hidden');
            if (st.temperature === 'critico') {
                document.getElementById('banner-titulo').textContent = `ALERTA TÉRMICO CRÍTICO: ${Number(d.temperature).toFixed(1)} °C`;
                document.getElementById('banner-descricao').textContent = 'Temperatura ultrapassou o limite máximo de 27.0 °C (ASHRAE TC 9.9). Verificar refrigeração dos racks.';
            }
        } else {
            banner.classList.add('hidden');
        }
    } catch (err) {
        console.error('Erro ao buscar /api/atual:', err);
    }
}

function atualizarCardKPI(campo, valor, casas, status) {
    const valEl = document.getElementById(`val-${campo}`);
    const badgeEl = document.getElementById(`badge-${campo}`);
    const cardEl = document.getElementById(`card-${campo}`);

    if (valor === null || valor === undefined) {
        valEl.textContent = '--';
    } else {
        valEl.textContent = Number(valor).toFixed(casas);
    }

    const rotulos = {
        normal: 'NORMAL',
        atencao: 'ATENÇÃO',
        critico: 'CRÍTICO',
        sem_dados: 'SEM DADOS'
    };

    badgeEl.textContent = rotulos[status] || '--';
    badgeEl.className = `kpi-status-badge ${status || ''}`;

    cardEl.classList.remove('estado-critico', 'estado-atencao');
    if (status === 'critico') cardEl.classList.add('estado-critico');
    if (status === 'atencao') cardEl.classList.add('estado-atencao');
}

// ================= GRÁFICOS & FILTRO TEMPORAL =================
function selecionarPeriodo(periodo, btn) {
    periodoAtual = periodo;
    document.getElementById('filtro-inicio').value = '';
    document.getElementById('filtro-fim').value = '';

    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    carregarHistorico();
}

function filtrarPersonalizado() {
    const inicio = document.getElementById('filtro-inicio').value;
    const fim = document.getElementById('filtro-fim').value;
    if (!inicio || !fim) {
        mostrarToast('Filtro Temporal', 'Selecione a data/hora inicial e final.', true);
        return;
    }
    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    carregarHistorico(inicio, fim);
}

async function carregarHistorico(inicio = null, fim = null) {
    try {
        let url = `/api/historico?periodo=${periodoAtual}`;
        if (inicio && fim) {
            url = `/api/historico?inicio=${encodeURIComponent(inicio)}&fim=${encodeURIComponent(fim)}`;
        }

        const resp = await fetch(url);
        const json = await resp.json();
        const regs = json.registros || [];

        document.getElementById('contador-pontos').textContent = `${regs.length} leituras`;

        const labels = regs.map(r => r.label_hora);
        const temps = regs.map(r => r.temperature);
        const hums = regs.map(r => r.humidity);
        const linhaLimite27 = regs.map(() => 27.0);

        const co2s = regs.map(r => r.CO2);
        const pm25 = regs.map(r => r.particula_1);
        const pm10 = regs.map(r => r.particula_2);

        chartTermico.data.labels = labels;
        chartTermico.data.datasets[0].data = temps;
        chartTermico.data.datasets[1].data = hums;
        chartTermico.data.datasets[2].data = linhaLimite27;
        chartTermico.update('none');

        chartAr.data.labels = labels;
        chartAr.data.datasets[0].data = co2s;
        chartAr.data.datasets[1].data = pm25;
        chartAr.data.datasets[2].data = pm10;
        chartAr.update('none');
    } catch (err) {
        console.error('Erro ao buscar /api/historico:', err);
    }
}

function inicializarGraficos() {
    Chart.defaults.color = '#94A3B8';
    Chart.defaults.font.family = "'JetBrains Mono', monospace";
    Chart.defaults.font.size = 11;

    const ctx1 = document.getElementById('graficoTermico').getContext('2d');
    chartTermico = new Chart(ctx1, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Temperatura (°C)',
                    data: [],
                    borderColor: '#38BDF8',
                    backgroundColor: 'rgba(56, 189, 248, 0.12)',
                    borderWidth: 2,
                    tension: 0.25,
                    pointRadius: 2,
                    spanGaps: false,
                    yAxisID: 'y'
                },
                {
                    label: 'Umidade (%)',
                    data: [],
                    borderColor: '#10B981',
                    backgroundColor: 'transparent',
                    borderWidth: 1.8,
                    tension: 0.25,
                    pointRadius: 1.5,
                    spanGaps: false,
                    yAxisID: 'y1'
                },
                {
                    label: 'Limite Crítico (27°C)',
                    data: [],
                    borderColor: '#EF4444',
                    borderWidth: 1.5,
                    borderDash: [6, 6],
                    pointRadius: 0,
                    fill: false,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { grid: { color: '#1E293B' }, ticks: { maxTicksLimit: 8 } },
                y: {
                    type: 'linear',
                    position: 'left',
                    grid: { color: '#1E293B' },
                    title: { display: true, text: '°C' }
                },
                y1: {
                    type: 'linear',
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: '%' }
                }
            }
        }
    });

    const ctx2 = document.getElementById('graficoAr').getContext('2d');
    chartAr = new Chart(ctx2, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'CO₂ (ppm)',
                    data: [],
                    borderColor: '#F59E0B',
                    borderWidth: 2,
                    tension: 0.25,
                    pointRadius: 1.5,
                    spanGaps: false,
                    yAxisID: 'y'
                },
                {
                    label: 'PM2.5 (µg/m³)',
                    data: [],
                    borderColor: '#EF4444',
                    borderWidth: 1.8,
                    tension: 0.25,
                    pointRadius: 1.5,
                    spanGaps: false,
                    yAxisID: 'y1'
                },
                {
                    label: 'PM10 (µg/m³)',
                    data: [],
                    borderColor: '#94A3B8',
                    borderWidth: 1.5,
                    tension: 0.25,
                    pointRadius: 1,
                    spanGaps: false,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { grid: { color: '#1E293B' }, ticks: { maxTicksLimit: 6 } },
                y: {
                    type: 'linear',
                    position: 'left',
                    grid: { color: '#1E293B' },
                    title: { display: true, text: 'ppm' }
                },
                y1: {
                    type: 'linear',
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: 'µg/m³' }
                }
            }
        }
    });
}

// ================= TABELA DE ALERTAS & TOAST INTERNO =================
async function carregarAlertas() {
    try {
        const resp = await fetch('/api/alertas');
        const json = await resp.json();
        const alertas = json.alertas || [];
        const tbody = document.getElementById('tabela-alertas');

        if (alertas.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Nenhum incidente registrado até o momento.</td></tr>';
            return;
        }

        // Dispara pop-up interno no canto inferior direito se surgiu um novo alerta
        const maisRecente = alertas[0];
        if (ultimoAlertaIdConhecido !== null && maisRecente.id > ultimoAlertaIdConhecido) {
            mostrarToast(`🚨 ${maisRecente.tipo_evento}`, maisRecente.mensagem_enviada, false);
        }
        ultimoAlertaIdConhecido = maisRecente.id;

        tbody.innerHTML = alertas.map(a => `
            <tr>
                <td class="mono">#${a.id}</td>
                <td class="mono">${a.data_alerta}</td>
                <td class="mono">ID ${a.id_leitura || '-'}</td>
                <td><strong style="color:#FCA5A5;">${a.tipo_evento}</strong></td>
                <td>${a.mensagem_enviada}</td>
                <td class="mono">${a.status_envio}</td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Erro ao carregar alertas:', err);
    }
}

function mostrarToast(titulo, mensagem, isInfo = false) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `noc-toast ${isInfo ? 'info' : ''}`;
    toast.innerHTML = `
        <div class="noc-toast-header">
            <span class="noc-toast-title">${titulo}</span>
            <button class="noc-toast-close" onclick="this.parentElement.parentElement.remove()">×</button>
        </div>
        <div class="noc-toast-body">${mensagem}</div>
    `;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 9000);
}

// ================= WEB PUSH (POP-UP DO WINDOWS / NAVEGADOR) =================
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

async function verificarStatusPushInicial() {
    if ('Notification' in window && Notification.permission === 'granted') {
        const btn = document.getElementById('btn-push');
        btn.textContent = '✓ Pop-up Ativo';
        btn.classList.add('ativo');
    }
}

async function ativarWebPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        mostrarToast('Navegador incompatível', 'Este navegador não suporta Web Push.', false);
        return;
    }

    try {
        const permissao = await Notification.requestPermission();
        if (permissao !== 'granted') {
            mostrarToast('Permissão Negada', 'Libere as notificações no ícone de cadeado ao lado da URL.', false);
            return;
        }

        const reg = await navigator.serviceWorker.register('/sw.js');
        await navigator.serviceWorker.ready;

        const respChave = await fetch('/api/push/chave');
        const { publicKey } = await respChave.json();

        const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicKey)
        });

        await fetch('/api/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub)
        });

        const btn = document.getElementById('btn-push');
        btn.textContent = '✓ Pop-up Ativo';
        btn.classList.add('ativo');
        mostrarToast('Pop-up Windows Ativado', 'Você receberá notificações nativas sempre que a temperatura passar de 27°C.', true);
    } catch (err) {
        console.error('Erro Web Push:', err);
        mostrarToast('Erro ao ativar Push', String(err), false);
    }
}

// ================= BOTÕES DE DEMONSTRAÇÃO =================
async function simularAlertaCritico() {
    try {
        const resp = await fetch('/api/simular-alerta', { method: 'POST' });
        const json = await resp.json();
        await atualizarTudo();
        mostrarToast('Simulação Executada', `Leitura #${json.leitura_id} (28.6 °C) gravada. Status: ${json.detalhe}`, false);
    } catch (err) {
        mostrarToast('Erro na simulação', String(err), false);
    }
}

async function testarResumoDiario() {
    try {
        const resp = await fetch('/api/teste-resumo-diario', { method: 'POST' });
        const json = await resp.json();
        mostrarToast('Relatório Diário (21:30)', json.mensagem, resp.ok);
    } catch (err) {
        mostrarToast('Erro ao testar resumo', String(err), false);
    }
}
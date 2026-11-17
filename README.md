# ProjetoIntegrador_MonitoramentoAmbiental

---

Documento Entrega 01
````
https://docs.google.com/document/d/1SWelCNJ3ZlKXP8CO6iwskIPryMgi8CHCjKZmKfnb3Cg/edit?usp=sharing
````
## Escopo Projeto

### Problema
Salas de servidores e pequenos data centers dependem de condições ambientais rigorosas para operar sem falhas. Oscilações no ar-condicionado, acúmulo de poeira nos coolers e superaquecimento de equipamentos podem causar desligamentos inesperados (*downtime*), queima de componentes e até princípios de incêndio.

### Justificativa
Sistemas comerciais de monitoramento ambiental para data centers possuem custo elevado e baixa flexibilidade. Implementar uma estação própria baseada em microcontroladores e protocolo MQTT oferece monitoramento contínuo, histórico de auditoria e alertas imediatos por uma fração do custo.

### Público-Alvo
Gestores de TI e Facilities responsáveis por salas de servidores, CPD e racks de telecomunicações.

### Objetivos
* **Geral:** Garantir a continuidade operacional e a integridade física de servidores por meio do monitoramento ambiental automatizado em tempo real.
* **Específicos:**
  * Monitorar continuamente temperatura, umidade, pressão, altitude, CO₂ e concentração de partículas (poeira/fumaça) dentro da sala técnica.
  * Transmitir telemetria de baixa latência via protocolo MQTT.
  * Armazenar séries históricas em banco de dados para análise de eficiência energética e auditoria de incidentes.
  * Disponibilizar os indicadores via API REST e Dashboard visual para a equipe de operações.
    
### Indicadores Escolhidos (Baseados em diretrizes ASHRAE para Data Centers)
* **Temperatura (`temperature`):** Controle de superaquecimento dos racks (faixa recomendada: **18 °C a 27 °C**).
* **Umidade Relativa (`humidity`):** Prevenção de descarga eletrostática (se muito baixa, < 40%) e condensação nos circuitos (se muito alta, > 60%).
* **Material Particulado (`particula1` - PM2.5 e `particula2` - PM10):** Monitoramento de poeira em suspensão (que obstrui dissipadores) e detecção precoce de fumaça/queima de componentes.
* **Concentração de CO₂ (`CO2`):** Indicador de renovação de ar, presença humana na sala restrita e detecção complementar de combustão.
* **Pressão Atmosférica (`pressure`) e Altitude (`altitude`):** Controle de pressurização positiva da sala (evita entrada de poeira externa) e calibração de fluxo de ar/dissipação térmica.




# Checklist Entregaveis
* Proposta do projeto✅
* Banco de dados [ ]
* Integração [ ]
* Dashboard [ ]
* Serviço externo (Api de notificação)[]
* Documentação e apresentação[]

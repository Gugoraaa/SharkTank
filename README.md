# Shark Tank — Simulador multiagente

Simulador de un episodio de Shark Tank: un emprendedor presenta su pitch y cinco Sharks (agentes con LLM) hacen preguntas por turnos, deliberan, dan retroalimentación y hacen o no una oferta. El emprendedor elige con quién cerrar el trato.

La interfaz vive en **Unity**; este repo es el backend en Python que orquesta la simulación y le manda los eventos a Unity por HTTP. También se puede jugar completo desde la terminal.

## Estructura del proyecto

```
shark-tank-app/
├── backend/
│   ├── main.py                  # Punto de entrada: servidor para Unity (default), --terminal, --auto, --server
│   ├── eventos_unity.py         # Servidor HTTP de eventos para Unity (librería estándar, puerto 8765)
│   ├── orquestador.py           # Orquestador determinista (etapas, turnos, historial, verificación)
│   ├── sharks.py                # Perfiles (personalidad + especialidad) y agentes Shark
│   ├── llm_simulado.py          # LLM simulado para trabajar sin API keys
│   ├── llm_openai.py            # LLM real con OpenAI (+ embeddings para preguntas repetidas)
│   ├── api.py                   # API FastAPI original (python main.py --server)
│   ├── test_ronda_preguntas.py  # Pruebas automáticas (usan el simulador)
│   ├── requirements.txt
│   ├── .env.example             # Plantilla de variables de entorno (cópiala a .env)
│   └── corridas/                # Historial de cada corrida (se genera solo)
└── README.md
```

## Instalación

Requisitos: Python 3.12.

```bash
cd backend
python -m venv venv
source venv/bin/activate      # En Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

El servidor para Unity, el modo terminal y el LLM simulado solo usan la librería estándar. `requirements.txt` hace falta para el LLM real (`openai`, `python-dotenv`) y para la API FastAPI (`--server`).

## Cómo correrlo

```bash
cd backend
python main.py              # servidor para Unity (0.0.0.0:8765): Unity crea la sesión y manda respuestas/elección
python main.py --terminal   # simulador interactivo en la terminal; tú respondes por escrito
python main.py --auto       # igual que --terminal, pero el emprendedor responde solo (corrida de prueba)
python main.py --server     # API FastAPI original (api.py) en el puerto 8000
python -m unittest test_ronda_preguntas -v   # pruebas automáticas con el simulador (no gastan tokens)
```

`--terminal` y `--auto` también levantan el servidor de eventos, así que Unity puede reproducir la corrida con `GET /eventos`.

## Flujo de la simulación

① Pitch (+ monto y porcentaje) → ② Preguntas por vueltas → ③ Deliberación y retroalimentación → ④ Ofertas (dentro/fuera) → ⑤ Elección.

- En cada vuelta, cada Shark pregunta o pasa. La ronda termina cuando todos pasan en la misma vuelta o se llega a `MAX_VUELTAS`.
- Cada pregunta y respuesta se reparte a todos los Sharks; las preguntas repetidas se rechazan.
- Oferta = f(interés): ninguno → fuera · bajo → pequeña (mitad del monto, mismo %) · medio → cercana (mismo monto, 1.5× el %) · alto → completa (lo pedido).
- Con el LLM real, las preguntas repetidas se detectan por significado (embeddings `text-embedding-3-small`, umbral 0.78 configurable con `UMBRAL_REPETIDAS`).

### Los Sharks

Cada Shark tiene una personalidad (forma de hablar, enfoque, apetito de riesgo) y además una especialidad de negocio, para que la mesa no haga cinco veces las mismas preguntas genéricas. Hablan en el orden de la tabla.

| Shark | Especialidad | Qué tan fácil es convencerlo | Máx. preguntas |
|---|---|---|---|
| Luffy | Marketing, marca y experiencia del cliente | muy fácil | 1 |
| Shanks | Ventas, alianzas y distribución | fácil | 2 |
| Zoro | Manufactura y operaciones | media | 2 |
| Whitebeard | Talento, cultura y crecimiento a largo plazo | difícil | 3 |
| Arlong | Finanzas: márgenes, valuación y deuda | muy difícil | 4 |

## Servidor para Unity (`python main.py`)

Servidor HTTP con la librería estándar, con CORS abierto. Cada sesión corre en su propio hilo y se queda esperando las respuestas y la elección que manda Unity. Crear una sesión nueva cancela la anterior.

| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| `POST` | `/sesion` | `{"emprendedor", "pitch", "monto", "porcentaje"}` | `{"sesion": id}` |
| `GET` | `/eventos?desde=N` | — | `{"sesion": id, "eventos": [...]}` (eventos con `seq > N`) |
| `GET` | `/estado` | — | `{"sesion", "total", "activa", "esperando": "nada" \| "respuesta" \| "eleccion"}` |
| `POST` | `/respuesta` | `{"texto": "..."}` | `{"ok": true}` |
| `POST` | `/eleccion` | `{"shark": "Luffy"}` o `{"shark": null}` | `{"ok": true}` |
| `POST` | `/cancelar` | — | `{"ok": true}` |

- Validación de `/sesion`: `emprendedor` y `pitch` no vacíos, `monto > 0` y `0 < porcentaje ≤ 100`.
- `/respuesta` y `/eleccion` responden `409` si la sesión no está esperando eso; `/eleccion` solo acepta Sharks que hicieron oferta (o `null` para no cerrar con nadie).
- Tipos de evento (campo `tipo`): `inicio_sesion`, `inicio_etapa`, `inicio_vuelta`, `turno`, `pitch`, `pregunta`, `pasar`, `respuesta`, `recomendacion`, `decision` (oferta dentro/fuera), `eleccion`, `esperando` (con `etapa`: `pitch`, `preguntas` o `eleccion`), `fin` (con el `resumen` de la verificación) y `error`.

Ejemplo de una sesión completa:

```bash
curl -X POST http://localhost:8765/sesion -H 'Content-Type: application/json' \
  -d '{"emprendedor": "Ana", "pitch": "App para pedir comida en la universidad", "monto": 100000, "porcentaje": 10}'
curl 'http://localhost:8765/eventos?desde=0'      # Unity hace polling con el último seq recibido
curl http://localhost:8765/estado                 # "esperando": "respuesta"
curl -X POST http://localhost:8765/respuesta -H 'Content-Type: application/json' -d '{"texto": "Vendemos 200 pedidos al día"}'
curl -X POST http://localhost:8765/eleccion  -H 'Content-Type: application/json' -d '{"shark": "Luffy"}'
```

## Configuración (`backend/.env`)

Por defecto los Sharks usan un LLM simulado (`llm_simulado.py`): no gasta tokens ni necesita internet. Las pruebas unitarias **siempre** usan el simulador.

Para usar OpenAI de verdad (`llm_openai.py`):

```env
USAR_LLM_REAL=1
API_TYPE=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini   # opcional
```

| Variable | Default | Para qué |
|---|---|---|
| `NUM_SHARKS` | 5 | Cuántos Sharks se sientan (1–5, en el orden de la tabla) |
| `MAX_VUELTAS` | 5 | Tope de vueltas en la ronda de preguntas |
| `SEMILLA` | — | Corridas reproducibles con el simulador |
| `UMBRAL_REPETIDAS` | 0.78 | Similitud a partir de la cual una pregunta cuenta como repetida (LLM real) |
| `UNITY_PORT` | 8765 | Puerto del servidor para Unity |
| `RITMO_AUTO` | 0 | Segundos de pausa entre eventos en `--auto` con el simulador, para ver la corrida en Unity |
| `BACKEND_PORT` | 8000 | Puerto de la API FastAPI (`--server`) |

Cada corrida se guarda en `backend/corridas/` con el historial y la verificación (preguntas repetidas, % de entrega a los Sharks).

## API FastAPI original (`python main.py --server`)

La API de la primera versión (migrada de los notebooks, con CrewAI) sigue disponible en `api.py`. Usa `API_TYPE=azure` con `AZURE_API_KEY`, `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT` y `AZURE_API_VERSION`, o `API_TYPE=openai` con `OPENAI_API_KEY`.

- `GET /`: health check
- `POST /api/test-connection`: prueba la conexión con el LLM
- `POST /api/start-pitch`: inicia una sesión de pitch
- `POST /api/send-message`: manda un mensaje en la conversación
- `GET /api/conversation-history`: historial de la conversación
- `POST /api/reset`: reinicia la conversación

### Ejemplos con curl

```bash
curl --location 'http://localhost:8000/'
```
```json
{
    "status": "Shark Tank Chat API is running"
}
```
```bash
curl --location --request POST 'http://localhost:8000/api/test-connection'
```
```json
{
    "status": "success",
    "message": "Connection test successful",
    "api_type": "AZURE",
    "response": "Hello, the connection is working! I am using the OpenAI API."
}
```
```bash
curl --location 'http://localhost:8000/api/start-pitch' \
--header 'Content-Type: application/json' \
--data '{
  "business_idea": {
    "name": "EcoTrack",
    "description": "A smart home energy management system that uses AI to optimize energy usage",
    "target_market": "Environmentally conscious homeowners and small businesses",
    "revenue_model": "Hardware sales + monthly subscription for premium features",
    "current_traction": "500 beta users, $50,000 in pre-orders",
    "investment_needed": "$300,000 for 10% equity",
    "use_of_funds": "Product refinement, marketing, and team expansion"
  },
  "judges": [
    {
      "name": "Mark Cuban",
      "role": "Shark Tank Judge",
      "goal": "Evaluate business pitches critically and decide whether to invest",
      "backstory": "You are a successful entrepreneur and investor on Shark Tank. You have a keen eye for promising businesses and can quickly identify flaws in business models. You are tough but fair, and you always ask penetrating questions about revenue, margins, customer acquisition costs, and scaling strategies. You'\''re not afraid to say '\''I'\''m out'\'' if the numbers don'\''t add up or if you don'\''t believe in the entrepreneur'\''s ability to execute."
    }
  ],
  "entrepreneur_name": "Alex Johnson"
}'
```
```json
{
    "response": "Responses from 1 judge(s)",
    "sender": "Judge",
    "conversation_history": [
        {
            "role": "Entrepreneur",
            "content": "Hello Sharks, I'm Alex Johnson, and today I'm excited to present to you EcoTrack, a revolutionary smart home energy management system that leverages the power of AI to optimize energy usage for environmentally conscious homeowners and small businesses.\n\nAs we all know, energy consumption is a leading contributor to climate change. Many homeowners and small businesses struggle to monitor and control their energy usage, leading to high costs and inefficiency. EcoTrack addresses this pressing issue by providing real-time energy monitoring, automated optimization suggestions, and AI-driven insights that empower users to reduce their carbon footprint and save money.\n\nOur target market is booming; with the rising awareness of sustainability, there are millions of environmentally conscious homeowners and small businesses looking for innovative solutions. We have already gained traction with 500 beta users and received $50,000 in pre-orders, validating our market demand and the effectiveness of our product.\n\nWhat sets EcoTrack apart is not just our cutting-edge technology but our team. Our diverse team combines years of expertise in AI, renewable energy, and consumer electronics. We are uniquely positioned to lead this market, offering a blend of technical innovation and user-friendly design that resonates with our target customers.\n\nToday, I'm seeking a $300,000 investment for 10% equity in EcoTrack. This funding will be instrumental in refining our product, ramping up marketing efforts, and expanding our team to meet growing demand. \n\nWith your investment, we can accelerate our growth, make a significant impact on energy consumption, and pave the way for a sustainable future. Thank you for considering EcoTrack as your next investment!",
            "sender_name": "Alex Johnson"
        },
        {
            "role": "Judge",
            "content": "Your final answer must include a thorough evaluation and critical questioning of the entrepreneur's pitch, focusing on the revenue model, customer acquisition costs, financial scalability, marketing differentiation, operational strategy, and potential challenges before making a final investment decision.",
            "judge_name": "Mark Cuban"
        }
    ]
}
```

## Solución de problemas

- **El puerto 8765 está ocupado**: cierra el otro proceso o cambia `UNITY_PORT`.
- **Unity no recibe eventos**: revisa que el backend esté corriendo y que Unity apunte a la IP y puerto correctos (el servidor escucha en `0.0.0.0`).
- **Error con el LLM real** (key inválida, sin saldo o sin conexión): la sesión publica un evento `error` con el detalle y lo imprime en consola. Para seguir trabajando, quita `USAR_LLM_REAL=1` y se usa el simulador.
- **API FastAPI / LiteLLM**: usa las versiones exactas de `requirements.txt` (`litellm==1.74.9`, `crewai==0.201.1`); revisa con `pip show litellm` y prueba la conexión con `/api/test-connection`.

## Licencia

Proyecto académico, basado en los notebooks originales de la clase.

"""
Shark Tank — punto de entrada.

    python main.py              -> servidor para Unity (0.0.0.0:$UNITY_PORT, default 8765): Unity crea la
                                   sesión (POST /sesion) y manda respuestas y elección; ver eventos_unity.py
    python main.py --terminal   -> simulador interactivo en la terminal (pitch, preguntas, retro, ofertas, elección)
    python main.py --auto       -> igual, pero el emprendedor responde solo (corrida de prueba rápida)
    python main.py --server     -> levanta la API FastAPI (api.py)

--terminal y --auto también publican sus eventos por HTTP (GET /eventos) para que Unity los muestre.

Por defecto los Sharks usan un LLM simulado (llm_simulado.py), sin API keys.
Para usar OpenAI de verdad, en backend/.env:  USAR_LLM_REAL=1, API_TYPE=openai, OPENAI_API_KEY=...
(ver llm_openai.py).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import eventos_unity
from eventos_unity import Sesion, SesionCancelada, publicar_evento
from orquestador import Orquestador, formatear_oferta, son_similares
from sharks import PERFILES_DEFAULT, SharkAgent

BASE = Path(__file__).parent

def cargar_env(ruta: Path) -> None:
    """Carga backend/.env. Usa python-dotenv si está instalado; si no, un lector simple
    (KEY=VALOR, ignora comentarios). No sobrescribe variables ya definidas en la terminal."""
    if not ruta.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ruta)
        return
    except ImportError:
        pass
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valor = valor.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(clave.strip(), valor)


cargar_env(BASE / ".env")


# ---------- Selección del "cerebro" de los Sharks ----------

def crear_sharks(num: int):
    """Devuelve (sharks, descripción del modo, comparador de preguntas repetidas)."""
    simulado = os.getenv("USAR_LLM_REAL") != "1"
    perfiles = PERFILES_DEFAULT[:num]
    if simulado:
        from llm_simulado import SimuladorLLM

        semilla = os.getenv("SEMILLA")
        base = int(semilla) if semilla and semilla.isdigit() else None
        sharks = [
            SharkAgent(p, SimuladorLLM(p, semilla=None if base is None else base + i))
            for i, p in enumerate(perfiles)
        ]
        return sharks, "simulado (sin API key; USAR_LLM_REAL=1 para el real)", son_similares
    from llm_openai import OpenAILLM

    llm = OpenAILLM()
    return [SharkAgent(p, llm) for p in perfiles], f"real ({llm.descripcion})", llm.comparador()


def config_sesion():
    """(número de Sharks, máximo de vueltas) desde NUM_SHARKS y MAX_VUELTAS."""
    num = os.getenv("NUM_SHARKS", "5")
    num = max(1, min(5, int(num))) if num.isdigit() else 5
    return num, int(os.getenv("MAX_VUELTAS", "5"))


def evento_inicio_sesion(emprendedor: str, sharks, modo: str) -> Dict:
    return {"tipo": "inicio_sesion", "emprendedor": emprendedor, "sharks": [s.nombre for s in sharks],
            "modo": modo, "monto": 0, "porcentaje": 0}


def evento_esperando_respuesta(emprendedor: str, nombre_shark: str, pregunta: str, orq: Orquestador) -> Dict:
    # El orquestador publica la pregunta y de inmediato pide la respuesta: es el último mensaje
    ultimo = orq.historial[-1] if orq.historial else {}
    es_ella = ultimo.get("tipo") == "pregunta" and ultimo.get("contenido") == pregunta
    return {"tipo": "esperando", "etapa": "preguntas", "emisor": emprendedor, "shark": nombre_shark,
            "pregunta": pregunta, "responde_a": ultimo["id"] if es_ella else None}


# ---------- Entrada / salida en terminal ----------

def leer_multilinea(titulo: str) -> str:
    print(titulo)
    print("(Escribe tu texto. Deja una línea vacía para terminar.)")
    while True:
        lineas = []
        while True:
            linea = input("> ")
            if not linea.strip():
                break
            lineas.append(linea)
        texto = "\n".join(lineas).strip()
        if texto:
            return texto
        print("El texto no puede estar vacío. Inténtalo de nuevo.")


def leer_numero(titulo: str, minimo: float = 0, maximo: float = float("inf")) -> float:
    while True:
        crudo = input(titulo).strip().replace("$", "").replace(",", "").replace("%", "")
        try:
            valor = float(crudo)
            if minimo < valor <= maximo:
                return valor
        except ValueError:
            pass
        print(f"Escribe un número mayor que {minimo:g}" + (f" y hasta {maximo:g}." if maximo != float("inf") else "."))


ICONO_INTERES = {"alto": "🟢", "medio": "🟡", "bajo": "🟠", "ninguno": "🔴"}


def imprimir_mensaje(mensaje: Dict) -> None:
    tipo = mensaje["tipo"]
    if tipo == "inicio_vuelta":
        print(f"\n{'=' * 22} Vuelta {mensaje['vuelta']} {'=' * 22}")
    elif tipo == "inicio_etapa":
        titulos = {"retroalimentacion": "③ RETROALIMENTACIÓN", "ofertas": "④ OFERTAS — ¿Dentro o fuera?"}
        titulo = titulos.get(mensaje["etapa"])  # "deliberacion" / "eleccion" ya se anuncian en main
        if titulo:
            print(f"\n{'=' * 60}\n{titulo}\n{'=' * 60}")
    elif tipo == "pregunta":
        print(f"\n🦈 {mensaje['emisor']} pregunta:\n   {mensaje['contenido']}")
    elif tipo == "pasar":
        print(f"\n🦈 {mensaje['emisor']} pasa.")
    elif tipo == "respuesta":
        print("   ✔ Respuesta registrada y compartida con todos los Sharks.")
    elif tipo == "recomendacion":
        print(f"\n🦈 {mensaje['emisor']} recomienda:\n   {mensaje['contenido']}")
    elif tipo == "decision":
        estado = "ESTOY DENTRO" if mensaje["dentro"] else "ESTOY FUERA"
        print(f"\n{ICONO_INTERES.get(mensaje['interes'], '')} {mensaje['emisor']}: {estado}  "
              f"(interés {mensaje['interes']})")
        print(f"   \"{mensaje['contenido']}\"")
        if mensaje["dentro"]:
            print(f"   Oferta: {formatear_oferta(mensaje['oferta'])}")
    elif tipo == "eleccion":
        print(f"\n🤝 {mensaje['contenido']}")


def imprimir_resumen(rep: Dict, max_vueltas: int) -> None:
    print("\n" + "=" * 60)
    print("RESUMEN DE LA SESIÓN")
    print("=" * 60)
    if rep["fin_por_consenso"]:
        print(f"✔ La ronda de preguntas terminó porque todos pasaron en la vuelta {rep['vueltas_jugadas']}.")
    else:
        print(f"⚠ La ronda de preguntas terminó por alcanzar el máximo de vueltas ({max_vueltas}).")
    rechazadas = sum(1 for r in rep["rechazos"] if r["motivo"] == "repetida")
    por_shark = ", ".join(f"{n} {c}" for n, c in rep["preguntas_por_shark"].items())
    print(f"Preguntas realizadas: {rep['total_preguntas']}  ({por_shark})")
    print(f"Preguntas repetidas en el historial: {len(rep['preguntas_repetidas'])}"
          f"  (intentos repetidos rechazados por el orquestador: {rechazadas})")
    print(f"Preguntas sin respuesta: {len(rep['preguntas_sin_respuesta'])}")
    print(f"Entrega de preguntas y respuestas a todos los Sharks: "
          f"{rep['entregas_qa_ok']}/{rep['entregas_qa_esperadas']} ({rep['porcentaje_entrega']}%)")
    print(f"Dentro: {', '.join(rep['dentro']) or 'nadie'}")
    print(f"Fuera:  {', '.join(rep['fuera']) or 'nadie'}")
    print(f"Trato:  {rep['eleccion'] or 'ninguno'}")


def guardar_corrida(nombre: str, sharks, modo: str, max_vueltas: int, orq: Orquestador, rep: Dict) -> Path:
    carpeta = BASE / "corridas"
    carpeta.mkdir(exist_ok=True)
    archivo = carpeta / f"corrida_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    archivo.write_text(json.dumps({
        "emprendedor": nombre,
        "modo_llm": modo,
        "sharks": [s.nombre for s in sharks],
        "max_vueltas": max_vueltas,
        "solicitud": {"monto": orq.monto, "porcentaje": orq.porcentaje},
        "historial": orq.historial,
        "verificacion": rep,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return archivo


# ---------- Programa ----------

def ejecutar_terminal(auto: bool = False) -> Dict:
    print("\n" + "=" * 60)
    print("            SHARK TANK — Simulador (modo terminal)")
    print("=" * 60)
    eventos_unity.iniciar_servidor()
    sesion = eventos_unity.nueva_sesion()
    if auto and os.getenv("USAR_LLM_REAL") != "1":  # pausa entre eventos para probar Unity
        try:
            eventos_unity.configurar_ritmo(float(os.getenv("RITMO_AUTO", "0")))
        except ValueError:
            pass

    num, max_vueltas = config_sesion()
    sharks, modo, es_similar = crear_sharks(num)
    print(f"Modo de los Sharks: {modo}")

    nombre = "Emprendedor de prueba" if auto else (input("\nTu nombre (emprendedor): ").strip() or "Emprendedor")
    publicar_evento(evento_inicio_sesion(nombre, sharks, modo))

    print("\nSharks en la mesa (del más fácil al más difícil de convencer):")
    for s in sharks:
        print(f"  • {s.nombre} — {s.perfil.facilidad_convencer} de convencer; se fija en {s.perfil.enfoque}")

    if auto:
        def pedir_respuesta(nombre_shark: str, pregunta: str) -> str:
            publicar_evento(evento_esperando_respuesta(nombre, nombre_shark, pregunta, orq))
            texto = f"Respuesta de prueba para {nombre_shark} sobre: {pregunta}"
            print(f"\n✍️  (auto) {texto}")
            return texto
    else:
        def pedir_respuesta(nombre_shark: str, pregunta: str) -> str:
            publicar_evento(evento_esperando_respuesta(nombre, nombre_shark, pregunta, orq))
            return leer_multilinea(f"\n✍️  Tu respuesta para {nombre_shark}:")

    orq = Orquestador(sharks=sharks, pedir_respuesta=pedir_respuesta, max_vueltas=max_vueltas,
                      notificar=lambda m: (imprimir_mensaje(m), publicar_evento(m)), es_similar=es_similar)

    # ① Pitch
    publicar_evento({"tipo": "esperando", "emisor": nombre, "etapa": "pitch"})
    if auto:
        pitch = "Hola Sharks, presento una app de prueba para pedir comida en la universidad."
        monto, porcentaje = 100_000, 10
        print(f"\n① PITCH (auto): {pitch}  Pido ${monto:,} por el {porcentaje}%.")
    else:
        pitch = leer_multilinea("\n① PITCH — Presenta tu negocio a los Sharks:")
        monto = leer_numero("¿Cuánto dinero pides? (ej. 1500000): $")
        porcentaje = leer_numero("¿Qué porcentaje de tu empresa ofreces? (ej. 10): ", maximo=100)
    orq.etapa_pitch(nombre, pitch, monto, porcentaje)

    # ② Preguntas
    print("\n② PREGUNTAS — Cada Shark pregunta o pasa por turnos; termina cuando todos pasan en una misma vuelta.")
    orq.etapa_preguntas(nombre)

    # ③ Retroalimentación y ④ Ofertas
    print("\nLos Sharks están deliberando...")
    orq.etapa_retro_y_ofertas()

    # ⑤ Elección
    def elegir(disponibles: Dict[str, Dict]) -> Optional[str]:
        nombres = list(disponibles)
        if auto:  # la mejor valuación (más dinero por cada punto porcentual)
            return max(nombres, key=lambda n: disponibles[n]["monto"] / disponibles[n]["porcentaje"])
        print(f"\n{'=' * 60}\n⑤ ELECCIÓN — ¿Con quién haces el trato?\n{'=' * 60}")
        for i, n in enumerate(nombres, 1):
            print(f"  {i}. {n}: {formatear_oferta(disponibles[n])}")
        print("  0. Ninguno")
        while True:
            opcion = input("Tu elección: ").strip()
            if opcion.isdigit() and 0 <= int(opcion) <= len(nombres):
                return nombres[int(opcion) - 1] if int(opcion) > 0 else None
            print(f"Escribe un número del 0 al {len(nombres)}.")

    if not any(orq.ofertas.values()):
        print("\nNingún Shark hizo oferta. 😢")
    orq.etapa_eleccion(nombre, elegir)

    rep = orq.verificar()
    publicar_evento({"tipo": "fin", "resumen": rep})
    imprimir_resumen(rep, max_vueltas)
    archivo = guardar_corrida(nombre, sharks, modo, max_vueltas, orq, rep)
    print(f"\nCorrida guardada en: {archivo}")
    sesion.terminar()
    return rep


# ---------- Modo servidor para Unity ----------

def ejecutar_sesion_unity(sesion: Sesion, datos: Dict) -> None:
    """Corre una sesión creada con POST /sesion (en su propio hilo). Las respuestas y la elección
    llegan por POST /respuesta y POST /eleccion."""
    nombre = datos["emprendedor"]
    try:
        num, max_vueltas = config_sesion()
        sharks, modo, es_similar = crear_sharks(num)
        print(f"\n{'=' * 60}\n📥 Sesión {sesion.id}: {nombre} pide ${datos['monto']:,.0f} "
              f"por el {datos['porcentaje']:g}%  (Sharks: {modo})\n{'=' * 60}")
        sesion.publicar(evento_inicio_sesion(nombre, sharks, modo))

        def pedir_respuesta(nombre_shark: str, pregunta: str) -> str:
            sesion.publicar(evento_esperando_respuesta(nombre, nombre_shark, pregunta, orq))
            print("   ⏳ Esperando la respuesta desde Unity...")
            texto = sesion.esperar("respuesta")
            print(f"   ✍️  {texto}")
            return texto

        def elegir(disponibles: Dict[str, Dict]) -> Optional[str]:
            sesion.publicar({"tipo": "esperando", "etapa": "eleccion", "emisor": nombre,
                             "opciones": [{"shark": n, **o} for n, o in disponibles.items()]})
            print("\n⏳ Esperando la elección desde Unity...")
            return sesion.esperar("eleccion", list(disponibles))

        # Primero se publica (lanza SesionCancelada si ya no es la sesión actual) y luego se imprime
        orq = Orquestador(sharks=sharks, pedir_respuesta=pedir_respuesta, max_vueltas=max_vueltas,
                          notificar=lambda m: (sesion.publicar(m), imprimir_mensaje(m)), es_similar=es_similar)
        resumen_pitch = datos["pitch"] if len(datos["pitch"]) <= 300 else datos["pitch"][:300] + "…"
        print(f"\n① PITCH:\n   {resumen_pitch}")
        orq.etapa_pitch(nombre, datos["pitch"], datos["monto"], datos["porcentaje"])
        print("\n② PREGUNTAS")
        orq.etapa_preguntas(nombre)
        print("\nLos Sharks están deliberando...")
        orq.etapa_retro_y_ofertas()
        if not any(orq.ofertas.values()):
            print("\nNingún Shark hizo oferta. 😢")
        orq.etapa_eleccion(nombre, elegir)

        rep = orq.verificar()
        sesion.publicar({"tipo": "fin", "resumen": rep})
        imprimir_resumen(rep, max_vueltas)
        archivo = guardar_corrida(nombre, sharks, modo, max_vueltas, orq, rep)
        print(f"\nCorrida guardada en: {archivo}\n📡 Esperando una nueva sesión desde Unity...")
    except SesionCancelada:
        print(f"\n🛑 Sesión {sesion.id} cancelada.")
    except Exception as e:  # p. ej. API key inválida, sin saldo o sin conexión
        contenido = f"{type(e).__name__}: {e}"
        print(f"\n❌ Error en la sesión {sesion.id}: {contenido}")
        try:
            sesion.publicar({"tipo": "error", "contenido": contenido})
        except SesionCancelada:
            pass
    finally:
        sesion.terminar()


def ejecutar_servidor_unity() -> None:
    print("\n" + "=" * 60)
    print("            SHARK TANK — Servidor para Unity")
    print("=" * 60)
    if not eventos_unity.iniciar_servidor():
        print("No se puede continuar sin el servidor. Cambia UNITY_PORT o cierra el proceso que usa el puerto.")
        sys.exit(1)
    eventos_unity.aceptar_sesiones(ejecutar_sesion_unity)
    real = os.getenv("USAR_LLM_REAL") == "1"
    print(f"LLM de los Sharks: {'real (OpenAI)' if real else 'simulado (USAR_LLM_REAL=1 para el real)'}")
    print("Esperando que Unity cree una sesión (POST /sesion)... Ctrl+C para salir.")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    if "--server" in sys.argv:
        import uvicorn

        from api import app

        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("BACKEND_PORT", 8000)))
    elif "--terminal" not in sys.argv and "--auto" not in sys.argv:
        try:
            ejecutar_servidor_unity()
        except KeyboardInterrupt:
            print("\nServidor detenido.")
    else:
        try:
            auto = "--auto" in sys.argv
            ejecutar_terminal(auto=auto)
            if eventos_unity.servidor_activo():  # deja que Unity termine de reproducir los eventos
                if auto:
                    print("\nServidor de eventos activo 120 s más (Ctrl+C para salir)...")
                    time.sleep(120)
                else:
                    input("\nPresiona Enter para salir")
        except (KeyboardInterrupt, EOFError):
            print("\nSesión terminada.")
        except Exception as e:  # p. ej. API key inválida, sin saldo o sin conexión
            print(f"\n❌ Error al consultar el LLM: {type(e).__name__}: {e}")
            print("Revisa OPENAI_API_KEY / saldo de la cuenta, o corre sin USAR_LLM_REAL para usar el simulador.")
            sys.exit(1)

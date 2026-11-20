"""
Shark Tank — punto de entrada.

    python main.py            -> simulador en la terminal (pitch, preguntas, retroalimentación, ofertas, elección)
    python main.py --auto     -> igual, pero el emprendedor responde solo (corrida de prueba rápida)
    python main.py --server   -> levanta la API FastAPI (api.py)

Por defecto los Sharks usan un LLM simulado (llm_simulado.py), sin API keys.
Para usar OpenAI de verdad, en backend/.env:  USAR_LLM_REAL=1, API_TYPE=openai, OPENAI_API_KEY=...
(ver llm_openai.py).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

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
        print(f"\n{'=' * 60}\n{titulos[mensaje['etapa']]}\n{'=' * 60}")
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

    num = os.getenv("NUM_SHARKS", "5")
    num = max(1, min(5, int(num))) if num.isdigit() else 5
    max_vueltas = int(os.getenv("MAX_VUELTAS", "5"))
    sharks, modo, es_similar = crear_sharks(num)
    print(f"Modo de los Sharks: {modo}")

    nombre = "Emprendedor de prueba" if auto else (input("\nTu nombre (emprendedor): ").strip() or "Emprendedor")

    print("\nSharks en la mesa (del más fácil al más difícil de convencer):")
    for s in sharks:
        print(f"  • {s.nombre} — {s.perfil.facilidad_convencer} de convencer; se fija en {s.perfil.enfoque}")

    if auto:
        def pedir_respuesta(nombre_shark: str, pregunta: str) -> str:
            texto = f"Respuesta de prueba para {nombre_shark} sobre: {pregunta}"
            print(f"\n✍️  (auto) {texto}")
            return texto
    else:
        def pedir_respuesta(nombre_shark: str, pregunta: str) -> str:
            return leer_multilinea(f"\n✍️  Tu respuesta para {nombre_shark}:")

    orq = Orquestador(sharks=sharks, pedir_respuesta=pedir_respuesta, max_vueltas=max_vueltas,
                      notificar=imprimir_mensaje, es_similar=es_similar)

    # ① Pitch
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
    imprimir_resumen(rep, max_vueltas)
    archivo = guardar_corrida(nombre, sharks, modo, max_vueltas, orq, rep)
    print(f"\nCorrida guardada en: {archivo}")
    return rep


if __name__ == "__main__":
    if "--server" in sys.argv:
        import uvicorn

        from api import app

        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("BACKEND_PORT", 8000)))
    else:
        try:
            ejecutar_terminal(auto="--auto" in sys.argv)
        except (KeyboardInterrupt, EOFError):
            print("\nSesión terminada.")
        except Exception as e:  # p. ej. API key inválida, sin saldo o sin conexión
            print(f"\n❌ Error al consultar el LLM: {type(e).__name__}: {e}")
            print("Revisa OPENAI_API_KEY / saldo de la cuenta, o corre sin USAR_LLM_REAL para usar el simulador.")
            sys.exit(1)

"""
Orquestador (software determinista, no es agente de IA).

Controla etapas y turnos, reparte el historial a todos los Sharks, rechaza preguntas
repetidas, valida las salidas de los agentes y limita el número de vueltas.

  ① Pitch            -> el emprendedor lo escribe (más cuánto pide y por qué porcentaje).
  ② Preguntas        -> vueltas por turnos; cada Shark pregunta o pasa; termina cuando todos
                        pasan en una misma vuelta (o al llegar al máximo de vueltas).
  ③ Retroalimentación -> cada Shark da al menos una recomendación.
  ④ Ofertas          -> cada Shark declara su nivel de interés; la oferta se calcula aquí:
                        Oferta = f(interés): ninguno -> fuera · bajo -> pequeña ·
                        medio -> cercana · alto -> completa.
  ⑤ Elección         -> la hace el emprendedor (un Shark o ninguno).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

from sharks import NIVELES_INTERES, SharkAgent

UMBRAL_SIMILITUD = 0.85  # comparación por texto: a partir de aquí dos preguntas son la misma


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def son_similares(a: str, b: str, umbral: float = UMBRAL_SIMILITUD) -> bool:
    """Comparador por texto (sin red). Detecta preguntas iguales o casi iguales."""
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= umbral


# ---------- Ofertas: f(nivel de interés) ----------

def calcular_oferta(interes: str, monto: float, porcentaje: float) -> Optional[Dict]:
    """Regla determinista del diagrama. Devuelve None si el Shark está fuera."""
    if interes == "alto":  # completa: lo que pidió el emprendedor
        return {"tipo": "completa", "monto": monto, "porcentaje": porcentaje}
    if interes == "medio":  # cercana: mismo monto, pide 50 % más de participación
        return {"tipo": "cercana", "monto": monto, "porcentaje": round(min(porcentaje * 1.5, 100), 1)}
    if interes == "bajo":  # pequeña: invierte la mitad por el mismo porcentaje
        return {"tipo": "pequeña", "monto": round(monto * 0.5, 2), "porcentaje": porcentaje}
    return None  # ninguno -> fuera


def formatear_oferta(oferta: Optional[Dict]) -> str:
    if not oferta:
        return "sin oferta"
    return f"${oferta['monto']:,.0f} por el {oferta['porcentaje']:g}% (oferta {oferta['tipo']})"


class Orquestador:
    def __init__(
        self,
        sharks: List[SharkAgent],
        pedir_respuesta: Callable[[str, str], str],
        max_vueltas: int = 8,
        notificar: Callable[[Dict], None] = lambda m: None,
        es_similar: Callable[[str, str], bool] = son_similares,
    ):
        """
        sharks: agentes participantes (1..5).
        pedir_respuesta(nombre_shark, pregunta) -> texto escrito por el emprendedor.
        notificar(mensaje): callback para mostrar cada mensaje (p. ej. imprimir en terminal).
        es_similar(a, b): decide si dos preguntas son la misma (texto o embeddings).
        """
        if not 1 <= len(sharks) <= 5:
            raise ValueError("Debe haber entre 1 y 5 Sharks")
        self.sharks = sharks
        self.pedir_respuesta = pedir_respuesta
        self.max_vueltas = max_vueltas
        self.notificar = notificar
        self.es_similar = es_similar
        self.historial: List[Dict] = []  # ambiente: historial compartido de la sesión
        self.rechazos: List[Dict] = []  # salidas rechazadas (repetidas / inválidas)
        self.vueltas_jugadas = 0
        self.fin_por_consenso = False
        self.monto = 0.0
        self.porcentaje = 0.0
        self.ofertas: Dict[str, Optional[Dict]] = {}  # nombre_shark -> oferta (None = fuera)
        self.eleccion: Optional[str] = None

    # ---------- Reparto del historial ----------
    def publicar(self, etapa: str, emisor: str, tipo: str, contenido: str, **extra) -> Dict:
        mensaje = {
            "id": len(self.historial) + 1,
            "etapa": etapa,
            "emisor": emisor,
            "tipo": tipo,
            "contenido": contenido,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **extra,
        }
        self.historial.append(mensaje)
        for shark in self.sharks:  # todos los Sharks reciben cada mensaje
            shark.percibir(mensaje)
        self.notificar(mensaje)
        return mensaje

    def preguntas_hechas(self) -> List[str]:
        return [m["contenido"] for m in self.historial if m["tipo"] == "pregunta"]

    def es_repetida(self, pregunta: str) -> bool:
        return any(self.es_similar(pregunta, previa) for previa in self.preguntas_hechas())

    def solicitud(self) -> str:
        return f"${self.monto:,.0f} por el {self.porcentaje:g}% de la empresa"

    # ---------- ① Pitch ----------
    def etapa_pitch(self, emprendedor: str, pitch: str, monto: float = 0, porcentaje: float = 0) -> None:
        self.monto, self.porcentaje = float(monto), float(porcentaje)
        contenido = pitch
        if monto and porcentaje:
            contenido += f"\n[Solicitud: {self.solicitud()}]"
        self.publicar("pitch", emprendedor, "pitch", contenido, monto=self.monto, porcentaje=self.porcentaje)

    # ---------- ② Preguntas ----------
    def _turno_shark(self, shark: SharkAgent) -> Optional[str]:
        """Pide la decisión al Shark y la valida. Devuelve la pregunta aceptada o None si pasa."""
        if shark.preguntas_propias() >= shark.limite_preguntas:
            return None  # llegó a su máximo: pasa sin consultar al LLM

        # Aviso (no va al historial): el Shark está "pensando" su turno
        self.notificar({"tipo": "turno", "emisor": shark.nombre, "etapa": "preguntas",
                        "vuelta": self.vueltas_jugadas})
        aviso = None
        for intento in range(2):  # un reintento si la salida no es válida o está repetida
            # Los errores de red/autenticación del LLM NO se ocultan: se propagan para que
            # no parezca que todos los Sharks "pasaron" cuando en realidad falló la API.
            decision = shark.decidir("preguntas", aviso=aviso)
            tipo, contenido = decision["tipo"], decision["contenido"]

            if tipo == "pasar":
                return None
            if tipo == "pregunta" and contenido:
                if not self.es_repetida(contenido):
                    return contenido
                motivo = "repetida"
                aviso = (
                    f'Tu pregunta "{contenido}" ya se hizo (o es equivalente a una anterior). '
                    "Pregunta sobre un tema distinto o responde con pasar."
                )
            else:
                motivo = "invalida"
                aviso = 'Tu salida no fue válida. Responde solo con el JSON {"tipo": ..., "contenido": ...}.'

            self.rechazos.append(
                {"shark": shark.nombre, "etapa": "preguntas", "vuelta": self.vueltas_jugadas,
                 "intento": intento + 1, "motivo": motivo, "salida": decision}
            )
        return None  # tras el reintento fallido, el turno cuenta como "pasar"

    def etapa_preguntas(self, emprendedor: str) -> None:
        for vuelta in range(1, self.max_vueltas + 1):
            self.vueltas_jugadas = vuelta
            self.notificar({"tipo": "inicio_vuelta", "vuelta": vuelta})
            alguien_pregunto = False

            for shark in self.sharks:
                pregunta = self._turno_shark(shark)
                if pregunta is None:
                    self.publicar("preguntas", shark.nombre, "pasar", "", vuelta=vuelta)
                    continue

                alguien_pregunto = True
                msg_q = self.publicar("preguntas", shark.nombre, "pregunta", pregunta, vuelta=vuelta)
                respuesta = self.pedir_respuesta(shark.nombre, pregunta)
                self.publicar(
                    "preguntas", emprendedor, "respuesta", respuesta,
                    vuelta=vuelta, responde_a=msg_q["id"],
                )

            if not alguien_pregunto:
                self.fin_por_consenso = True
                return

    # ---------- ③ Retroalimentación y ④ Ofertas ----------
    @staticmethod
    def _validar_evaluacion(ev: Dict[str, str]) -> Optional[str]:
        """Devuelve el motivo del rechazo, o None si la salida es válida y coherente."""
        if not ev["recomendacion"]:
            return "falta la recomendación"
        if ev["decision"] not in ("dentro", "fuera"):
            return 'la "decision" debe ser exactamente "dentro" o "fuera"'
        if ev["interes"] not in NIVELES_INTERES:
            return 'el "interes" debe ser exactamente: ninguno, bajo, medio o alto'
        if (ev["decision"] == "fuera") != (ev["interes"] == "ninguno"):
            return 'incoherente: "fuera" va con interés "ninguno"; "dentro" con bajo, medio o alto'
        return None

    def _evaluar_shark(self, shark: SharkAgent) -> Dict[str, str]:
        aviso = None
        for intento in range(2):
            ev = shark.evaluar(self.solicitud(), aviso=aviso)
            motivo = self._validar_evaluacion(ev)
            if motivo is None:
                return ev
            self.rechazos.append({"shark": shark.nombre, "etapa": "ofertas", "intento": intento + 1,
                                  "motivo": "invalida", "detalle": motivo, "salida": ev})
            aviso = f"Tu salida no fue válida: {motivo}. Corrígela y responde solo con el JSON."
        # Sin salida válida tras el reintento: manda la decisión explícita; si no hay, fuera
        if ev["decision"] == "dentro" and ev["interes"] in ("bajo", "medio", "alto"):
            interes = ev["interes"]
        elif ev["decision"] == "dentro":
            interes = "bajo"
        else:
            interes = "ninguno"
        return {"recomendacion": ev["recomendacion"] or "Sin recomendación.",
                "decision": "dentro" if interes != "ninguno" else "fuera",
                "interes": interes, "comentario": ev["comentario"] or "Sin comentario."}

    def etapa_retro_y_ofertas(self) -> None:
        # Cada Shark evalúa sin ver la decisión de los demás (se publican al final)
        self.notificar({"tipo": "inicio_etapa", "etapa": "deliberacion"})
        evaluaciones = [(shark, self._evaluar_shark(shark)) for shark in self.sharks]

        self.notificar({"tipo": "inicio_etapa", "etapa": "retroalimentacion"})
        for shark, ev in evaluaciones:
            self.publicar("retroalimentacion", shark.nombre, "recomendacion", ev["recomendacion"])

        self.notificar({"tipo": "inicio_etapa", "etapa": "ofertas"})
        for shark, ev in evaluaciones:
            oferta = calcular_oferta(ev["interes"], self.monto, self.porcentaje)
            self.ofertas[shark.nombre] = oferta
            self.publicar(
                "ofertas", shark.nombre, "decision", ev["comentario"],
                interes=ev["interes"], dentro=oferta is not None, oferta=oferta,
            )

    # ---------- ⑤ Elección ----------
    def etapa_eleccion(self, emprendedor: str, elegir: Callable[[Dict[str, Dict]], Optional[str]]) -> None:
        """elegir(ofertas_disponibles) -> nombre del Shark elegido o None (ninguno)."""
        self.notificar({"tipo": "inicio_etapa", "etapa": "eleccion"})
        disponibles = {n: o for n, o in self.ofertas.items() if o}
        eleccion = elegir(disponibles) if disponibles else None
        if eleccion is not None and eleccion not in disponibles:
            raise ValueError(f"{eleccion} no hizo oferta")
        self.eleccion = eleccion
        texto = (f"Acepto la oferta de {eleccion}: {formatear_oferta(disponibles[eleccion])}"
                 if eleccion else "No acepto ninguna oferta.")
        self.publicar("eleccion", emprendedor, "eleccion", texto, shark_elegido=eleccion)

    # ---------- Verificación de criterios de aceptación ----------
    def verificar(self) -> Dict:
        preguntas = [m for m in self.historial if m["tipo"] == "pregunta"]
        respuestas = [m for m in self.historial if m["tipo"] == "respuesta"]

        repetidas = []
        for i, a in enumerate(preguntas):
            for b in preguntas[i + 1:]:
                if self.es_similar(a["contenido"], b["contenido"]):
                    repetidas.append((a["id"], b["id"]))

        ids_qa = [m["id"] for m in preguntas + respuestas]
        entregas_esperadas = len(ids_qa) * len(self.sharks)
        entregas_ok = sum(
            1 for shark in self.sharks for mid in ids_qa
            if mid in {r["id"] for r in shark.recibidos}
        )
        preguntas_sin_respuesta = [
            q["id"] for q in preguntas if not any(r.get("responde_a") == q["id"] for r in respuestas)
        ]
        excedidos = [
            s.nombre for s in self.sharks
            if sum(1 for q in preguntas if q["emisor"] == s.nombre) > s.limite_preguntas
        ]
        con_recomendacion = {m["emisor"] for m in self.historial if m["tipo"] == "recomendacion" and m["contenido"]}

        return {
            "vueltas_jugadas": self.vueltas_jugadas,
            "fin_por_consenso": self.fin_por_consenso,
            "total_preguntas": len(preguntas),
            "preguntas_por_shark": {s.nombre: sum(1 for q in preguntas if q["emisor"] == s.nombre)
                                    for s in self.sharks},
            "sharks_que_excedieron_limite": excedidos,
            "preguntas_repetidas": repetidas,
            "preguntas_sin_respuesta": preguntas_sin_respuesta,
            "entregas_qa_ok": entregas_ok,
            "entregas_qa_esperadas": entregas_esperadas,
            "porcentaje_entrega": 100.0 if entregas_esperadas == 0 else round(100 * entregas_ok / entregas_esperadas, 2),
            "sharks_sin_recomendacion": [s.nombre for s in self.sharks if s.nombre not in con_recomendacion]
                                        if self.ofertas else [],
            "dentro": [n for n, o in self.ofertas.items() if o],
            "fuera": [n for n, o in self.ofertas.items() if not o],
            "eleccion": self.eleccion,
            "rechazos": self.rechazos,
        }

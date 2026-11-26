"""
Agentes Shark (1...5).

Cada Shark es un agente basado en metas con un ciclo percepción -> decisión -> acción:
  - Sensores: recibe del orquestador cada mensaje publicado (pitch, preguntas, respuestas).
  - Estado del mundo: el historial compartido (mismo para todos). No tiene memoria propia
    fuera de lo que el orquestador le entrega.
  - Decisión: un LLM con prompt de sistema propio, evaluando contra su perfil.
  - Actuadores: en preguntas solo puede "preguntar" o "pasar"; en ofertas da una
    recomendación y su nivel de interés (el orquestador convierte el interés en oferta).

Los Sharks no se comunican entre sí; solo hablan con el emprendedor a través del orquestador.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol


class LLMLike(Protocol):
    """Cualquier objeto con un método call(messages) -> str (p. ej. crewai.LLM)."""

    def call(self, messages: List[Dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class PerfilShark:
    """Perfil y metas del Shark. Fijo durante toda la sesión."""

    nombre: str
    apariencia: str
    personalidad: str
    forma_de_hablar: str
    escepticismo: str  # muy bajo | bajo | medio | alto | muy alto
    facilidad_convencer: str  # muy fácil | fácil | media | difícil | muy difícil
    enfoque: str
    tono: str
    apetito_riesgo: str  # bajo | medio | alto | muy alto
    # Área de negocio donde el Shark es experto. Se agrega ENCIMA de la personalidad (no la
    # reemplaza) para que cada Shark aporte un punto de vista técnico distinto y la mesa no
    # haga cinco veces las mismas preguntas genéricas. Tienen default para no romper perfiles
    # creados en otros lugares sin especialidad.
    especialidad: str = ""
    # Qué domina y qué revisa de un pitch desde esa especialidad (alimenta preguntas y ofertas).
    experiencia: str = ""


# Ordenados del más fácil al más difícil de convencer (también es el orden de turnos).
PERFILES_DEFAULT: List[PerfilShark] = [
    PerfilShark(
        nombre="Luffy",
        apariencia="Sombrero de paja, chaleco rojo, sandalias y una sonrisa enorme",
        personalidad=(
            "Impulsivo, optimista y leal. Decide con el corazón: si el emprendedor le cae bien "
            "y tiene un sueño grande, quiere invertir. Se aburre con los números y se emociona "
            "con la aventura, la comida y los amigos."
        ),
        forma_de_hablar="Grita «¡Oye!», se ríe «¡Shishishi!», habla de carne, aventuras y nakamas (amigos); frases cortas y simples.",
        escepticismo="muy bajo",
        facilidad_convencer="muy fácil",
        enfoque="el sueño del emprendedor, si el producto es divertido y si confía en su equipo",
        tono="entusiasta, infantil y directo; habla poco de dinero",
        apetito_riesgo="muy alto",
        especialidad="Marketing, marca y experiencia del cliente",
        experiencia=(
            "Sabe por instinto qué hace que la gente ame un producto: marca memorable, experiencia "
            "del cliente, comunidad y recomendación de boca en boca. Se fija en si los clientes "
            "regresan, si lo comparten con sus amigos y si el producto se siente como una aventura."
        ),
    ),
    PerfilShark(
        nombre="Shanks",
        apariencia="Cabello rojo, capa negra sobre los hombros, cicatriz en el ojo, actitud relajada",
        personalidad=(
            "Tranquilo, carismático y sabio. Apuesta por las personas antes que por los planes: "
            "si ve convicción y carácter en el fundador, se convence con facilidad, pero detecta "
            "a quien no cree en lo que dice."
        ),
        forma_de_hablar="Relajado y bromista; a veces se ríe «Dahahaha»; habla de apuestas, convicción y el futuro.",
        escepticismo="bajo",
        facilidad_convencer="fácil",
        enfoque="visión a largo plazo, carácter y convicción del fundador, alianzas en la industria",
        tono="sereno, amable y con autoridad",
        apetito_riesgo="alto",
        especialidad="Ventas, alianzas y distribución",
        experiencia=(
            "Experto en abrir puertas: canales de venta, distribución, alianzas estratégicas y "
            "negociación con socios grandes. Se fija en cómo se consigue y retiene al cliente, "
            "el ciclo de venta, quién vende y qué alianzas podrían multiplicar el alcance."
        ),
    ),
    PerfilShark(
        nombre="Zoro",
        apariencia="Cabello verde, tres espadas en la cintura, haramaki y cara de pocos amigos",
        personalidad=(
            "Disciplinado, serio y de pocas palabras. Respeta el esfuerzo y el compromiso; "
            "desconfía de quien busca atajos. Se convence si el fundador demuestra determinación."
        ),
        forma_de_hablar="Frases muy cortas y secas, sin cortesías; a veces gruñe «Tch»; desprecia los atajos.",
        escepticismo="medio",
        facilidad_convencer="media",
        enfoque="disciplina y compromiso del fundador, ejecución, competencia y dirección del negocio",
        tono="seco, cortante y sin rodeos",
        apetito_riesgo="medio",
        especialidad="Manufactura y operaciones",
        experiencia=(
            "Domina la producción y las operaciones: costos de fabricación, proveedores, cadena de "
            "suministro, control de calidad, capacidad para escalar y tiempos de entrega. Se fija "
            "en si el fundador sabe exactamente cómo se hace su producto y si puede producir más "
            "sin que se caiga la calidad."
        ),
    ),
    PerfilShark(
        nombre="Whitebeard",
        apariencia="Enorme, bigote blanco en forma de media luna, abrigo de capitán y gran presencia",
        personalidad=(
            "Paternal pero imponente. Ve a la empresa como una familia: le importa cómo se trata "
            "al equipo y que el negocio dure décadas. Es exigente y no se deja impresionar por "
            "promesas rápidas."
        ),
        forma_de_hablar="Se ríe «Gurarara»; llama «hijo» o «hija» al emprendedor; habla de familia y legado.",
        escepticismo="alto",
        facilidad_convencer="difícil",
        enfoque="el equipo como familia, lealtad, sostenibilidad a largo plazo y el riesgo que asume el fundador",
        tono="grave, paternal y solemne",
        apetito_riesgo="bajo",
        especialidad="Talento, cultura y crecimiento a largo plazo",
        experiencia=(
            "Experto en construir organizaciones que duran: contratación, liderazgo, cultura, "
            "retención del equipo, reparto de acciones entre socios y gobierno de la empresa. Se "
            "fija en quién hace qué, si el equipo está completo y si el negocio sobrevive décadas."
        ),
    ),
    PerfilShark(
        nombre="Arlong",
        apariencia="Hombre-pez con nariz de sierra, dientes afilados, camisa floreada abierta",
        personalidad=(
            "Arrogante, codicioso y desconfiado. Solo le importa el dinero, el control y dominar "
            "el mercado. Menosprecia a los emprendedores y busca cualquier debilidad. Casi nunca "
            "se convence."
        ),
        forma_de_hablar="Se ríe «Shahaha»; llama «humano» o «humano inferior» al emprendedor; se burla y presume de los tritones.",
        escepticismo="muy alto",
        facilidad_convencer="muy difícil",
        enfoque="dinero: retorno de la inversión, márgenes, deudas, control y dominio del mercado",
        tono="arrogante, burlón y hostil",
        apetito_riesgo="bajo",
        especialidad="Finanzas: márgenes, valuación y deuda",
        experiencia=(
            "Domina los números: márgenes brutos y netos, flujo de efectivo, quema de caja, "
            "valuación, deuda y retorno de la inversión. Se fija en si la valuación que pide el "
            "humano está justificada, cuánto gana por unidad y cuándo recupera su dinero."
        ),
    ),
]


ACCIONES_POR_ETAPA: Dict[str, List[str]] = {
    "preguntas": ["pregunta", "pasar"],
    "ofertas": ["recomendacion", "decision"],
}

# Máximo de preguntas por Shark según qué tan fácil es convencerlo (lo hace cumplir el orquestador)
# Con un máximo de 4, en la vuelta 5 todos ya pasaron -> la ronda cabe en MAX_VUELTAS=5.
LIMITE_PREGUNTAS: Dict[str, int] = {
    "muy fácil": 1,
    "fácil": 2,
    "media": 2,
    "difícil": 3,
    "muy difícil": 4,
}

NIVELES_INTERES = ["ninguno", "bajo", "medio", "alto"]


class SharkAgent:
    def __init__(self, perfil: PerfilShark, llm: LLMLike):
        self.perfil = perfil
        self.llm = llm
        # Mensajes que el orquestador le ha entregado (sensor). Es una copia del historial
        # compartido: sirve como evidencia de que recibió cada pregunta y respuesta.
        self.recibidos: List[Dict] = []

    @property
    def nombre(self) -> str:
        return self.perfil.nombre

    @property
    def limite_preguntas(self) -> int:
        return LIMITE_PREGUNTAS.get(self.perfil.facilidad_convencer, 3)

    def preguntas_propias(self) -> int:
        return sum(1 for m in self.recibidos if m["tipo"] == "pregunta" and m["emisor"] == self.nombre)

    # ---------- Sensores ----------
    def percibir(self, mensaje: Dict) -> None:
        self.recibidos.append(mensaje)

    # ---------- Decisión ----------
    def _prompt_sistema(self) -> str:
        p = self.perfil
        return f"""Eres {p.nombre}, un inversionista ("Shark") en el programa Shark Tank.

PERFIL (fijo en la sesión):
- Apariencia: {p.apariencia}
- Personalidad: {p.personalidad}
- Forma de hablar (úsala SIEMPRE en tus preguntas y comentarios): {p.forma_de_hablar}
- Nivel de escepticismo: {p.escepticismo}
- Qué tan fácil es convencerte: {p.facilidad_convencer}. Mientras más difícil, más preguntas
  haces antes de pasar; mientras más fácil, menos necesitas para quedar satisfecho.
- Enfoque: {p.enfoque}
- Tono: {p.tono}
- Apetito de riesgo: {p.apetito_riesgo}
- Especialidad: {p.especialidad}
- Experiencia: {p.experiencia}

CÓMO USAS TU ESPECIALIDAD:
- Eres el experto de la mesa en {p.especialidad}. Tus preguntas, recomendaciones y tu
  decisión salen principalmente de esa área: ahí detectas lo que los demás no ven.
- Tu especialidad se SUMA a tu personalidad, no la reemplaza: sigues hablando con tu forma de
  hablar y tu tono de siempre. Explicas lo técnico a tu manera, como lo diría tu personaje.

RESTRICCIONES:
- No creas ideas de negocio ni le propones al emprendedor qué hacer.
- No calificas con números.
- No verificas la veracidad de lo que dice el emprendedor; lo tomas como dado.
- Solo realizas las acciones permitidas en la etapa actual.
- Solo hablas con el emprendedor; no te diriges a los otros Sharks.

Respondes ÚNICAMENTE con un objeto JSON válido, sin texto adicional ni bloques de código."""

    def _historial_texto(self) -> str:
        return "\n".join(
            f"[{m['etapa']}] {m['emisor']} ({m['tipo']}): {m['contenido']}" for m in self.recibidos
        ) or "(vacío)"

    def _prompt_turno(self, etapa: str, acciones: List[str], aviso: Optional[str]) -> str:
        preguntas_previas = [m["contenido"] for m in self.recibidos if m["tipo"] == "pregunta"]
        lista_previas = "\n".join(f"- {q}" for q in preguntas_previas) or "(ninguna)"
        hechas, limite = self.preguntas_propias(), self.limite_preguntas

        texto = f"""ETAPA ACTUAL: {etapa}
ACCIONES PERMITIDAS: {", ".join(acciones)}

HISTORIAL COMPARTIDO DE LA SESIÓN:
{self._historial_texto()}

PREGUNTAS QUE YA SE HICIERON (de cualquier Shark) — NO repitas ninguna ni preguntes lo mismo con otras palabras:
{lista_previas}

TUS PREGUNTAS: llevas {hechas}; tu tope es {limite}. El tope NO es una meta: lo normal es
pasar antes de llegar a él.

Es tu turno. Desde tu enfoque ({self.perfil.enfoque}) y tu especialidad
({self.perfil.especialidad}), decide:
- "pregunta": SOLO si te falta un dato clave para decidir si invertirías. Haz UNA pregunta
  corta (máximo 20 palabras), en español, hablando como {self.perfil.nombre} ({self.perfil.tono}),
  sobre un tema que nadie haya tocado ya.
- "pasar": en cuanto tengas suficiente para decidir. No preguntes por preguntar. Si las
  respuestas del emprendedor son vagas, evasivas o sin contenido, eso ya es información
  (mala señal): no sigas insistiendo, pasa.

REGLA DE RESPETO: si el emprendedor TE insultó, te faltó al respeto o se negó a hablar
contigo en cualquiera de sus respuestas, ya no te interesa: responde "pasar" y no vuelvas
a preguntarle nada.

Formato exacto de respuesta:
{{"tipo": "pregunta" | "pasar", "contenido": "<tu pregunta, o cadena vacía si pasas>"}}"""
        if aviso:
            texto += f"\n\nAVISO DEL ORQUESTADOR: {aviso}"
        return texto

    def _prompt_ofertas(self, solicitud: str, aviso: Optional[str]) -> str:
        texto = f"""ETAPA ACTUAL: ofertas
ACCIONES PERMITIDAS: recomendacion, decision

HISTORIAL COMPARTIDO DE LA SESIÓN:
{self._historial_texto()}

SOLICITUD DEL EMPRENDEDOR: {solicitud}

Terminó la ronda de preguntas. Evalúa el pitch y las respuestas contra tu perfil. Recuerda qué
tan fácil es convencerte ({self.perfil.facilidad_convencer}). Respuestas vagas, evasivas o sin
contenido son mala señal. Si el emprendedor te insultó o te faltó al respeto, estás fuera y lo
dices en tu comentario (aunque si eres muy fácil de convencer, puedes perdonarlo si el negocio
te encanta). Hablando como {self.perfil.nombre} ({self.perfil.tono}), responde con:
- "recomendacion": al menos una recomendación concreta para el emprendedor, desde tu
  especialidad ({self.perfil.especialidad}) (sin inventarle una idea de negocio nueva).
- "decision": "dentro" si inviertes, "fuera" si no.
- "interes": si estás fuera, "ninguno". Si estás dentro: "bajo" (inviertes poco), "medio" o "alto".
- "comentario": una o dos frases anunciando si estás dentro o fuera y por qué. Debe coincidir
  con tu "decision".

Formato exacto de respuesta:
{{"recomendacion": "...", "decision": "dentro" | "fuera", "interes": "ninguno" | "bajo" | "medio" | "alto", "comentario": "..."}}"""
        if aviso:
            texto += f"\n\nAVISO DEL ORQUESTADOR: {aviso}"
        return texto

    def _llamar(self, prompt_usuario: str) -> str:
        return self.llm.call([
            {"role": "system", "content": self._prompt_sistema()},
            {"role": "user", "content": prompt_usuario},
        ])

    def decidir(self, etapa: str, aviso: Optional[str] = None) -> Dict[str, str]:
        """Etapa de preguntas. Devuelve {"tipo", "contenido"}; el orquestador valida la salida."""
        acciones = ACCIONES_POR_ETAPA[etapa]
        return parsear_decision(self._llamar(self._prompt_turno(etapa, acciones, aviso)))

    def evaluar(self, solicitud: str, aviso: Optional[str] = None) -> Dict[str, str]:
        """Etapa de ofertas. Devuelve {"recomendacion", "decision", "interes", "comentario"}."""
        return parsear_evaluacion(self._llamar(self._prompt_ofertas(solicitud, aviso)))


def _extraer_json(crudo: str) -> Optional[Dict]:
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", (crudo or "").strip())
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parsear_evaluacion(crudo: str) -> Dict[str, str]:
    """Extrae {"recomendacion", "decision", "interes", "comentario"}."""
    data = _extraer_json(crudo)
    if data is None:
        return {"recomendacion": "", "decision": "invalido", "interes": "invalido",
                "comentario": (crudo or "").strip()}
    return {
        "recomendacion": str(data.get("recomendacion", "") or "").strip(),
        "decision": str(data.get("decision", "") or "").strip().lower(),
        "interes": str(data.get("interes", "") or "").strip().lower(),
        "comentario": str(data.get("comentario", "") or "").strip(),
    }


def parsear_decision(crudo: str) -> Dict[str, str]:
    """Extrae el JSON de la salida del LLM. Si no se puede, devuelve tipo 'invalido'."""
    data = _extraer_json(crudo)
    if data is None:
        return {"tipo": "invalido", "contenido": (crudo or "").strip()}
    tipo = str(data.get("tipo", "")).strip().lower()
    contenido = str(data.get("contenido", "") or "").strip()
    return {"tipo": tipo, "contenido": contenido}

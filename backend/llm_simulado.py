"""
LLM simulado para trabajar SIN API keys.

Implementa la misma interfaz que crewai.LLM (call(messages) -> str) y devuelve el JSON
{"tipo": "pregunta" | "pasar", "contenido": ...} que espera el SharkAgent.

Cada Shark tiene un banco de preguntas según su personaje y enfoque. En cada turno:
  - lee del prompt las preguntas que ya se hicieron y elige una nueva de su banco;
  - pasa cuando ya hizo su cuota de preguntas (aleatoria, mayor mientras más difícil
    sea de convencer) o se le acabó el banco;
  - con cierta probabilidad intenta REPETIR una pregunta ya hecha, para simular el error
    típico de un LLM y comprobar que el orquestador la rechaza.
"""

from __future__ import annotations

import json
import random
import re
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from sharks import PerfilShark

BANCOS_PREGUNTAS: Dict[str, List[str]] = {
    "Luffy": [
        "¡Oye! ¿Cuál es tu sueño con este negocio?",
        "¿Tu producto es divertido? ¿Por qué te emociona tanto?",
        "¿Tus amigos trabajan contigo? ¿Confías en ellos?",
        "¿Puedo probar tu producto ahora mismo?",
    ],
    "Shanks": [
        "¿Qué te hizo apostar por esta idea y no por otra?",
        "¿Cómo ves tu negocio dentro de diez años?",
        "¿Qué aliados tienes en la industria?",
        "Si esto fracasara, ¿volverías a intentarlo? ¿Por qué?",
    ],
    "Zoro": [
        "¿Cuántas horas al día le dedicas a esto?",
        "¿Qué hiciste la última vez que algo salió mal en el negocio?",
        "¿Quién es tu competidor más fuerte y cómo piensas vencerlo?",
        "¿Hacia dónde va exactamente tu negocio en los próximos tres años?",
        "¿Cuál es el siguiente paso concreto que vas a dar con el dinero?",
    ],
    "Whitebeard": [
        "¿Cómo tratas a tu equipo? ¿Son una familia o solo empleados?",
        "¿Qué pasa con tu gente si el negocio tiene un mal año?",
        "¿Este negocio puede sostenerse por décadas o es una moda?",
        "¿Cuánto has arriesgado tú de tu propio bolsillo?",
        "¿Quién depende de este negocio además de ti?",
    ],
    "Arlong": [
        "¿Cuánto dinero me vas a devolver y en cuánto tiempo, humano?",
        "¿Cuál es tu margen real, sin cuentos?",
        "¿Qué porcentaje del mercado controlas hoy?",
        "¿Qué deudas tiene tu empresa?",
        "¿Qué impide que alguien más grande te aplaste mañana?",
        "¿Cuánto has arriesgado tú de tu propio bolsillo?",  # se cruza con Whitebeard a propósito
    ],
}

PREGUNTAS_GENERICAS = [
    "¿Cuánto dinero has invertido tú personalmente en el negocio?",
    "¿Cuál es el mayor riesgo que ves en tu negocio?",
    "¿En qué vas a usar exactamente la inversión que pides?",
]

# Cuántas preguntas hace antes de pasar (mín, máx), según qué tan fácil es convencerlo
# (siempre <= LIMITE_PREGUNTAS de sharks.py)
CUOTA_POR_FACILIDAD: Dict[str, Tuple[int, int]] = {
    "muy fácil": (1, 1),
    "fácil": (1, 2),
    "media": (2, 2),
    "difícil": (2, 3),
    "muy difícil": (3, 4),
}

# Interés base según qué tan fácil es convencerlo (luego se ajusta por la calidad de las respuestas)
INTERES_BASE: Dict[str, float] = {
    "muy fácil": 3.0,
    "fácil": 2.3,
    "media": 1.6,
    "difícil": 1.0,
    "muy difícil": 0.3,
}

VEREDICTOS: Dict[str, Dict[str, str]] = {
    "Luffy": {
        "recomendacion": "¡Haz que tu producto sea divertido y comparte la aventura con tus clientes!",
        "dentro": "¡Me caes bien y tu sueño es enorme! ¡Estoy dentro!",
        "fuera": "Mmm... no me emocionó. Estoy fuera, ¡pero suerte en tu aventura!",
    },
    "Shanks": {
        "recomendacion": "Rodéate de aliados que crean en tu visión tanto como tú.",
        "dentro": "Veo convicción en ti. Apuesto por ti: estoy dentro.",
        "fuera": "Todavía no veo la convicción que busco. Por ahora estoy fuera.",
    },
    "Zoro": {
        "recomendacion": "Deja de buscar atajos y enfócate en ejecutar un paso a la vez.",
        "dentro": "Tienes determinación. Estoy dentro.",
        "fuera": "Te falta disciplina. Estoy fuera.",
    },
    "Whitebeard": {
        "recomendacion": "Cuida a tu gente: un negocio que no protege a su familia no dura.",
        "dentro": "Gurarara... veo una familia sólida detrás de este negocio. Estoy dentro.",
        "fuera": "No veo un negocio capaz de proteger a los suyos por décadas. Estoy fuera.",
    },
    "Arlong": {
        "recomendacion": "Si quieres sobrevivir, conoce tus números al centavo, humano.",
        "dentro": "Shahaha... tus números me convencen, por ahora. Estoy dentro, con mis condiciones.",
        "fuera": "Shahaha, ¡ningún humano me hará perder mi dinero! Estoy fuera.",
    },
}
VEREDICTO_GENERICO = {
    "recomendacion": "Define mejor tus números y tu plan de crecimiento.",
    "dentro": "Me convenciste. Estoy dentro.",
    "fuera": "No me convenciste. Estoy fuera.",
}


class SimuladorLLM:
    def __init__(
        self,
        perfil: "PerfilShark",
        semilla: Optional[int] = None,
        prob_repetir: float = 0.2,
        max_preguntas: Optional[int] = None,
    ):
        self.rng = random.Random(semilla)
        self.banco = list(BANCOS_PREGUNTAS.get(perfil.nombre, PREGUNTAS_GENERICAS))
        self.rng.shuffle(self.banco)
        self.prob_repetir = prob_repetir
        # Cuántas preguntas hará este Shark antes de pasar (varía entre corridas y
        # depende de qué tan difícil es convencerlo: Luffy pregunta poco, Arlong mucho)
        minimo, maximo = CUOTA_POR_FACILIDAD.get(perfil.facilidad_convencer, (1, 3))
        self.cuota = max_preguntas if max_preguntas is not None else self.rng.randint(minimo, maximo)
        self.hechas = 0
        self.perfil = perfil

    @staticmethod
    def _preguntas_previas(prompt: str) -> List[str]:
        bloque = re.search(r"PREGUNTAS QUE YA SE HICIERON.*?\n(.*?)\n\n", prompt, re.DOTALL)
        if not bloque:
            return []
        return [l[2:].strip() for l in bloque.group(1).splitlines() if l.startswith("- ")]

    def call(self, messages: List[Dict[str, str]]) -> str:
        prompt = messages[-1]["content"]
        if prompt.startswith("ETAPA ACTUAL: ofertas"):
            return self._evaluar(prompt)
        previas = self._preguntas_previas(prompt)
        hubo_aviso = "AVISO DEL ORQUESTADOR" in prompt

        if self.hechas >= self.cuota:
            return json.dumps({"tipo": "pasar", "contenido": ""})

        # Simula el error de un LLM: repetir una pregunta ya hecha (solo si no lo acaban de corregir)
        if previas and not hubo_aviso and self.rng.random() < self.prob_repetir:
            return json.dumps({"tipo": "pregunta", "contenido": self.rng.choice(previas)}, ensure_ascii=False)

        previas_set = set(previas)
        disponibles = [q for q in self.banco if q not in previas_set]
        if not disponibles:
            return json.dumps({"tipo": "pasar", "contenido": ""})

        pregunta = disponibles[0]
        self.banco.remove(pregunta)
        self.hechas += 1
        return json.dumps({"tipo": "pregunta", "contenido": pregunta}, ensure_ascii=False)

    def _evaluar(self, prompt: str) -> str:
        """Interés = base por personalidad + calidad de las respuestas (longitud) + azar."""
        respuestas = re.findall(r"\(respuesta\): (.*)", prompt)
        promedio = sum(len(r) for r in respuestas) / len(respuestas) if respuestas else 40
        puntos = INTERES_BASE.get(self.perfil.facilidad_convencer, 1.8)
        if promedio >= 80:
            puntos += 0.7
        elif promedio >= 30:
            puntos += 0.2
        elif promedio < 15:
            puntos -= 1.2  # respuestas como "a" o "sí": mala señal
        puntos += self.rng.uniform(-0.5, 0.5)

        interes = "ninguno" if puntos < 0.8 else "bajo" if puntos < 1.6 else "medio" if puntos < 2.4 else "alto"
        v = VEREDICTOS.get(self.perfil.nombre, VEREDICTO_GENERICO)
        comentario = v["fuera"] if interes == "ninguno" else v["dentro"]
        return json.dumps({"recomendacion": v["recomendacion"],
                           "decision": "fuera" if interes == "ninguno" else "dentro", "interes": interes,
                           "comentario": comentario}, ensure_ascii=False)

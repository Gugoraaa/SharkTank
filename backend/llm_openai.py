"""
LLM real para los Sharks usando directamente la librería oficial de OpenAI.

Misma interfaz que el simulador: call(messages) -> str.
Solo requiere:  pip install openai python-dotenv

Configuración en backend/.env:
    USAR_LLM_REAL=1
    API_TYPE=openai
    OPENAI_API_KEY=sk-...
    OPENAI_MODEL=gpt-4o-mini        # opcional (default gpt-4o-mini, barato)

También funciona con Azure OpenAI (API_TYPE=azure + AZURE_API_KEY, AZURE_ENDPOINT,
AZURE_DEPLOYMENT, AZURE_API_VERSION).
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List

UMBRAL_EMBEDDINGS = float(os.getenv("UMBRAL_REPETIDAS", "0.78"))


class OpenAILLM:
    def __init__(self):
        try:
            from openai import AzureOpenAI, OpenAI
        except ImportError as e:
            raise SystemExit("Falta la librería de OpenAI. Instálala con:  pip install openai") from e

        api_type = os.getenv("API_TYPE", "openai").lower()
        if api_type == "azure":
            faltan = [v for v in ("AZURE_API_KEY", "AZURE_ENDPOINT") if not os.getenv(v)]
            if faltan:
                raise SystemExit(f"Faltan variables en .env: {', '.join(faltan)}")
            self.client = AzureOpenAI(
                api_key=os.environ["AZURE_API_KEY"],
                azure_endpoint=os.environ["AZURE_ENDPOINT"],
                api_version=os.getenv("AZURE_API_VERSION", "2024-02-01"),
            )
            self.model = os.getenv("AZURE_DEPLOYMENT", "gpt-4o-mini")
        else:
            if not os.getenv("OPENAI_API_KEY"):
                raise SystemExit("Falta OPENAI_API_KEY en backend/.env")
            self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.descripcion = f"{api_type}:{self.model}"

        self._cache_embeddings: Dict[str, List[float]] = {}
        # En Azure el modelo de embeddings es un deployment aparte (opcional)
        self.modelo_embeddings = (os.getenv("AZURE_EMBEDDINGS_DEPLOYMENT") if api_type == "azure"
                                  else os.getenv("OPENAI_EMBEDDINGS_MODEL", "text-embedding-3-small"))

    def _embedding(self, texto: str) -> List[float]:
        if texto not in self._cache_embeddings:
            r = self.client.embeddings.create(model=self.modelo_embeddings, input=texto)
            self._cache_embeddings[texto] = r.data[0].embedding
        return self._cache_embeddings[texto]

    def comparador(self, umbral: float = UMBRAL_EMBEDDINGS) -> Callable[[str, str], bool]:
        """Devuelve es_similar(a, b) para el orquestador: igual por texto O por significado.
        Calibrado con una corrida real: paráfrasis >= 0.78, preguntas distintas < 0.77."""
        from orquestador import son_similares

        if not self.modelo_embeddings:
            return son_similares

        def es_similar(a: str, b: str) -> bool:
            if son_similares(a, b):
                return True
            ea, eb = self._embedding(a), self._embedding(b)
            return sum(x * y for x, y in zip(ea, eb)) >= umbral  # vectores ya normalizados

        return es_similar

    def call(self, messages: List[Dict[str, str]]) -> str:
        respuesta = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.7,
            max_tokens=300,  # pregunta corta o veredicto breve; limita el gasto por turno
            response_format={"type": "json_object"},  # fuerza JSON válido
        )
        return respuesta.choices[0].message.content or ""

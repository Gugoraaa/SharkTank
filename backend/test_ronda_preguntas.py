"""
Pruebas del Sprint 1 (sin API keys): ronda de preguntas por turnos.

    cd backend && python -m unittest test_ronda_preguntas -v

Criterios verificados en N corridas con el LLM simulado:
  3. Cada Shark pregunta o pasa; la ronda termina cuando todos pasan en una misma vuelta;
     no hay preguntas repetidas en el historial.
  4. El emprendedor responde cada pregunta y el 100 % de los Sharks recibe cada
     pregunta y su respuesta.
Además: límite de preguntas por personalidad, retroalimentación (>= 1 recomendación por
Shark), ofertas = f(interés) y elección del emprendedor.
"""

import json
import unittest

from llm_simulado import SimuladorLLM
from orquestador import Orquestador, calcular_oferta, son_similares
from sharks import PERFILES_DEFAULT, SharkAgent

N_CORRIDAS = 200


def correr(semilla: int, num_sharks: int = 5, prob_repetir: float = 0.3,
           respuesta=lambda s, q: f"Respuesta detallada para {s} sobre la pregunta: {q}") -> Orquestador:
    sharks = [
        SharkAgent(p, SimuladorLLM(p, semilla=semilla * 10 + i, prob_repetir=prob_repetir))
        for i, p in enumerate(PERFILES_DEFAULT[:num_sharks])
    ]
    orq = Orquestador(sharks, pedir_respuesta=respuesta, max_vueltas=5)
    orq.etapa_pitch("Emprendedor", "Pitch de prueba", monto=1_000_000, porcentaje=10)
    orq.etapa_preguntas("Emprendedor")
    orq.etapa_retro_y_ofertas()
    orq.etapa_eleccion("Emprendedor", lambda ofertas: next(iter(ofertas)))
    return orq


class TestRondaPreguntas(unittest.TestCase):
    def test_corridas(self):
        rechazos_totales = 0
        for semilla in range(N_CORRIDAS):
            for num in (1, 3, 5):
                orq = correr(semilla, num)
                rep = orq.verificar()
                with self.subTest(semilla=semilla, sharks=num):
                    self.assertTrue(rep["fin_por_consenso"])
                    self.assertEqual(rep["preguntas_repetidas"], [])
                    self.assertEqual(rep["preguntas_sin_respuesta"], [])
                    self.assertEqual(rep["porcentaje_entrega"], 100.0)
                    self.assertGreater(rep["total_preguntas"], 0)

                    # La última vuelta: todos los Sharks pasaron
                    ultima = [m for m in orq.historial if m.get("vuelta") == rep["vueltas_jugadas"]]
                    self.assertEqual(len(ultima), num)
                    self.assertTrue(all(m["tipo"] == "pasar" for m in ultima))

                    # En las vueltas anteriores, cada Shark tuvo exactamente un turno
                    for v in range(1, rep["vueltas_jugadas"]):
                        turnos = [m["emisor"] for m in orq.historial
                                  if m.get("vuelta") == v and m["tipo"] in ("pregunta", "pasar")]
                        self.assertEqual(turnos, [s.nombre for s in orq.sharks])

                    # Cada pregunta va seguida inmediatamente de su respuesta
                    for i, m in enumerate(orq.historial):
                        if m["tipo"] == "pregunta":
                            self.assertEqual(orq.historial[i + 1]["tipo"], "respuesta")
                            self.assertEqual(orq.historial[i + 1]["responde_a"], m["id"])

                    # Mensaje JSON {etapa, emisor, tipo, contenido}
                    for m in orq.historial:
                        self.assertTrue({"etapa", "emisor", "tipo", "contenido"} <= m.keys())
                        json.dumps(m)
                    # Límite de preguntas por personalidad
                    self.assertEqual(rep["sharks_que_excedieron_limite"], [])
                    # ③ cada Shark dio al menos una recomendación; ④ todos decidieron
                    self.assertEqual(rep["sharks_sin_recomendacion"], [])
                    self.assertEqual(sorted(rep["dentro"] + rep["fuera"]), sorted(s.nombre for s in orq.sharks))
                    for m in orq.historial:
                        if m["tipo"] == "decision":
                            self.assertEqual(m["oferta"], calcular_oferta(m["interes"], 1_000_000, 10))
                    # ⑤ la elección es un Shark que está dentro (o ninguno si nadie ofreció)
                    self.assertEqual(rep["eleccion"], rep["dentro"][0] if rep["dentro"] else None)
                rechazos_totales += sum(1 for r in rep["rechazos"] if r["motivo"] == "repetida")
        # El simulador intenta repetir a propósito: el orquestador debió rechazar algunas
        self.assertGreater(rechazos_totales, 0)

    def test_luffy_mas_facil_que_arlong(self):
        """Los más difíciles de convencer preguntan más en promedio."""
        conteo = {p.nombre: 0 for p in PERFILES_DEFAULT}
        for semilla in range(N_CORRIDAS):
            for m in correr(semilla).historial:
                if m["tipo"] == "pregunta":
                    conteo[m["emisor"]] += 1
        orden = [p.nombre for p in PERFILES_DEFAULT]  # del más fácil al más difícil
        self.assertEqual(sorted(orden, key=lambda n: conteo[n]), orden, conteo)

    def test_oferta_segun_interes(self):
        self.assertIsNone(calcular_oferta("ninguno", 1000, 10))
        self.assertEqual(calcular_oferta("bajo", 1000, 10), {"tipo": "pequeña", "monto": 500, "porcentaje": 10})
        self.assertEqual(calcular_oferta("medio", 1000, 10), {"tipo": "cercana", "monto": 1000, "porcentaje": 15})
        self.assertEqual(calcular_oferta("alto", 1000, 10), {"tipo": "completa", "monto": 1000, "porcentaje": 10})

    def test_respuestas_vagas_convencen_menos(self):
        """Responder 'a' a todo produce menos Sharks dentro que responder con detalle."""
        buenas = sum(len(correr(s).verificar()["dentro"]) for s in range(N_CORRIDAS))
        vagas = sum(len(correr(s, respuesta=lambda *_: "a").verificar()["dentro"]) for s in range(N_CORRIDAS))
        self.assertLess(vagas, buenas)

    def test_luffy_entra_mas_que_arlong(self):
        dentro = {p.nombre: 0 for p in PERFILES_DEFAULT}
        for semilla in range(N_CORRIDAS):
            for n in correr(semilla).verificar()["dentro"]:
                dentro[n] += 1
        self.assertGreater(dentro["Luffy"], dentro["Arlong"], dentro)

    def test_dice_fuera_con_interes_bajo_queda_fuera(self):
        """Caso visto con el LLM real: comentario 'estoy fuera' pero interés 'bajo'."""
        class LLMIncoherente:
            def call(self, messages):
                if messages[-1]["content"].startswith("ETAPA ACTUAL: ofertas"):
                    return json.dumps({"recomendacion": "Cuida tus costos.", "decision": "fuera",
                                       "interes": "bajo", "comentario": "Estoy fuera."})
                return json.dumps({"tipo": "pasar", "contenido": ""})

        orq = Orquestador([SharkAgent(PERFILES_DEFAULT[4], LLMIncoherente())], pedir_respuesta=lambda s, q: "x")
        orq.etapa_pitch("E", "pitch", 1000, 10)
        orq.etapa_preguntas("E")
        orq.etapa_retro_y_ofertas()
        rep = orq.verificar()
        self.assertEqual(rep["fuera"], ["Arlong"])
        self.assertEqual([r["etapa"] for r in rep["rechazos"]], ["ofertas", "ofertas"])

    def test_rechaza_reformulacion_casi_identica(self):
        self.assertTrue(son_similares("¿Cuál es tu margen bruto?", "cual es tu margen bruto"))
        self.assertFalse(son_similares("¿Cuál es tu margen bruto?", "¿Quién fabrica tu producto?"))

    def test_salida_invalida_cuenta_como_pasar(self):
        class LLMRoto:
            def call(self, messages):
                return "no es json"

        orq = Orquestador([SharkAgent(PERFILES_DEFAULT[0], LLMRoto())],
                          pedir_respuesta=lambda s, q: "x")
        orq.etapa_pitch("E", "pitch", 1000, 10)
        orq.etapa_preguntas("E")
        orq.etapa_retro_y_ofertas()
        rep = orq.verificar()
        self.assertTrue(rep["fin_por_consenso"])
        self.assertEqual(rep["vueltas_jugadas"], 1)
        self.assertEqual(rep["fuera"], ["Luffy"])  # salida inválida en ofertas -> fuera
        self.assertEqual(len(rep["rechazos"]), 4)  # 2 en preguntas + 2 en ofertas


if __name__ == "__main__":
    unittest.main()

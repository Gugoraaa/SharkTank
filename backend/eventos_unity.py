"""
Servidor HTTP para Unity (solo librería estándar).

Unity consulta los eventos de la sesión actual y, en modo servidor, también la controla:

    GET  /eventos?desde=N  -> {"sesion": id|null, "eventos": [eventos con seq > N, en orden]}
    GET  /estado           -> {"sesion": id|null, "total": n, "activa": bool,
                               "esperando": "nada" | "respuesta" | "eleccion"}
    POST /sesion           {"emprendedor", "pitch", "monto", "porcentaje"} -> {"sesion": id}
    POST /respuesta        {"texto": "..."}          -> {"ok": true}
    POST /eleccion         {"shark": "Luffy" | null} -> {"ok": true}
    POST /cancelar                                   -> {"ok": true}

Cada sesión tiene su propio id y su lista de eventos (seq empieza en 1). Al crear una sesión
nueva, la anterior se cancela: su hilo se desbloquea con SesionCancelada y ya no publica nada.

Puerto: variable de entorno UNITY_PORT (default 8765).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

MAX_CUERPO = 1_000_000  # bytes aceptados en un POST (pitches largos caben de sobra)

_lock = threading.RLock()
_actual: Optional["Sesion"] = None
_servidor: Optional[ThreadingHTTPServer] = None
_ritmo = 0.0  # segundos de pausa entre eventos (RITMO_AUTO, solo --auto con LLM simulado)
_al_crear_sesion: Optional[Callable[["Sesion", Dict], None]] = None  # solo en modo servidor


class SesionCancelada(Exception):
    """Se lanza dentro del hilo de una sesión que fue cancelada o reemplazada."""


class Sesion:
    def __init__(self) -> None:
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.eventos: List[Dict] = []
        self.activa = True
        self.cancelada = False
        self.esperando = "nada"  # "nada" | "respuesta" | "eleccion"
        self._opciones: List[str] = []  # sharks elegibles mientras se espera la elección
        self._valor: Any = None
        self._hay_valor = False
        self._cond = threading.Condition(_lock)

    def publicar(self, evento: Dict) -> Dict:
        """Guarda una copia del evento con su seq. Si la sesión fue cancelada, lanza SesionCancelada."""
        with _lock:
            if self.cancelada:
                raise SesionCancelada()
            copia = {**evento, "seq": len(self.eventos) + 1}
            self.eventos.append(copia)
        if _ritmo:
            time.sleep(_ritmo)
        return copia

    def esperar(self, tipo: str, opciones: Optional[List[str]] = None) -> Any:
        """Bloquea el hilo de la sesión hasta que llegue POST /respuesta o /eleccion."""
        with self._cond:
            if self.cancelada:
                raise SesionCancelada()
            self.esperando, self._opciones = tipo, opciones or []
            self._valor, self._hay_valor = None, False
            while not self._hay_valor and not self.cancelada:
                self._cond.wait()
            self.esperando, self._opciones = "nada", []
            if self.cancelada:
                raise SesionCancelada()
            self._hay_valor = False
            return self._valor

    def entregar(self, tipo: str, valor: Any) -> Tuple[int, Dict]:
        """Lo llaman los manejadores HTTP. Devuelve (código, cuerpo)."""
        with self._cond:
            if self.esperando != tipo or self.cancelada:
                que = "pregunta" if tipo == "respuesta" else "elección"
                return 409, {"error": f"no hay {que} pendiente"}
            if tipo == "eleccion" and valor is not None and valor not in self._opciones:
                return 400, {"error": f"{valor} no hizo oferta; opciones: {', '.join(self._opciones)} o null"}
            self._valor, self._hay_valor = valor, True
            self._cond.notify_all()
            return 200, {"ok": True}

    def cancelar(self) -> None:
        with self._cond:
            self.cancelada = True
            self.activa = False
            self.esperando = "nada"
            self._cond.notify_all()

    def terminar(self) -> None:
        with _lock:
            self.activa = False
            self.esperando = "nada"


# ---------- API para main.py ----------

def configurar_ritmo(segundos: float) -> None:
    global _ritmo
    _ritmo = max(0.0, segundos)


def nueva_sesion() -> Sesion:
    """Cancela la sesión actual (si hay) y crea una nueva vacía."""
    global _actual
    with _lock:
        if _actual is not None:
            _actual.cancelar()
        _actual = Sesion()
        return _actual


def sesion_actual() -> Optional[Sesion]:
    return _actual


def publicar_evento(evento: Dict) -> Optional[Dict]:
    """Publica en la sesión actual (modo terminal/--auto)."""
    sesion = _actual
    return sesion.publicar(evento) if sesion else None


def aceptar_sesiones(callback: Callable[[Sesion, Dict], None]) -> None:
    """Modo servidor: callback(sesion, datos) se ejecuta en un hilo daemon por cada POST /sesion."""
    global _al_crear_sesion
    _al_crear_sesion = callback


# ---------- Validación de POST /sesion ----------

def _numero(valor: Any) -> Optional[float]:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor.strip().replace("$", "").replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def validar_sesion(datos: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    emprendedor, pitch = datos.get("emprendedor"), datos.get("pitch")
    if not isinstance(emprendedor, str) or not emprendedor.strip():
        return None, "El nombre del emprendedor no puede estar vacío."
    if not isinstance(pitch, str) or not pitch.strip():
        return None, "El pitch no puede estar vacío."
    monto, porcentaje = _numero(datos.get("monto")), _numero(datos.get("porcentaje"))
    if monto is None or not monto > 0:
        return None, "El monto debe ser un número mayor que 0."
    if porcentaje is None or not 0 < porcentaje <= 100:
        return None, "El porcentaje debe ser un número mayor que 0 y hasta 100."
    return {"emprendedor": emprendedor.strip(), "pitch": pitch.strip(),
            "monto": monto, "porcentaje": porcentaje}, None


# ---------- HTTP ----------

class _Manejador(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _responder(self, codigo: int, cuerpo: Dict) -> None:
        datos = json.dumps(cuerpo, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _leer_json(self) -> Tuple[Optional[Dict], Optional[str]]:
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "Content-Length inválido."
        if largo > MAX_CUERPO:
            return None, "El cuerpo es demasiado grande."
        crudo = self.rfile.read(largo) if largo > 0 else b""
        if not crudo.strip():
            return {}, None
        try:
            datos = json.loads(crudo.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "El cuerpo debe ser JSON válido en UTF-8."
        if not isinstance(datos, dict):
            return None, "El cuerpo debe ser un objeto JSON."
        return datos, None

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        sesion = _actual
        if url.path == "/eventos":
            crudo = parse_qs(url.query).get("desde", ["0"])[0]
            try:
                desde = max(0, int(crudo))
            except ValueError:
                self._responder(400, {"error": "desde debe ser un entero"})
                return
            with _lock:
                eventos = list(sesion.eventos[desde:]) if sesion else []  # seq = índice + 1
            self._responder(200, {"sesion": sesion.id if sesion else None, "eventos": eventos})
        elif url.path == "/estado":
            with _lock:
                cuerpo = {"sesion": sesion.id if sesion else None,
                          "total": len(sesion.eventos) if sesion else 0,
                          "activa": bool(sesion and sesion.activa),
                          "esperando": sesion.esperando if sesion else "nada"}
            self._responder(200, cuerpo)
        else:
            self._responder(404, {"error": "ruta no encontrada"})

    def do_POST(self) -> None:  # noqa: N802
        ruta = urlparse(self.path).path
        if ruta not in ("/sesion", "/respuesta", "/eleccion", "/cancelar"):
            self._responder(404, {"error": "ruta no encontrada"})
            return
        datos, error = self._leer_json()
        if error:
            self._responder(400, {"error": error})
            return
        sesion = _actual

        if ruta == "/sesion":
            if _al_crear_sesion is None:
                self._responder(409, {"error": "este modo no acepta sesiones desde Unity (corre python main.py)"})
                return
            valido, error = validar_sesion(datos)
            if error:
                self._responder(400, {"error": error})
                return
            nueva = nueva_sesion()
            threading.Thread(target=_al_crear_sesion, args=(nueva, valido),
                             name=f"sesion-{nueva.id}", daemon=True).start()
            self._responder(200, {"sesion": nueva.id})

        elif ruta == "/respuesta":
            texto = datos.get("texto")
            if not isinstance(texto, str) or not texto.strip():
                self._responder(400, {"error": "La respuesta no puede estar vacía."})
                return
            if sesion is None:
                self._responder(409, {"error": "no hay pregunta pendiente"})
                return
            self._responder(*sesion.entregar("respuesta", texto.strip()))

        elif ruta == "/eleccion":
            if "shark" not in datos:
                self._responder(400, {"error": 'Falta el campo "shark" (nombre o null).'})
                return
            shark = datos["shark"]
            if shark is not None and not isinstance(shark, str):
                self._responder(400, {"error": '"shark" debe ser un nombre o null.'})
                return
            if isinstance(shark, str):
                shark = shark.strip() or None
            if sesion is None:
                self._responder(409, {"error": "no hay elección pendiente"})
                return
            self._responder(*sesion.entregar("eleccion", shark))

        else:  # /cancelar
            if sesion is not None:
                sesion.cancelar()
            self._responder(200, {"ok": True})

    def log_message(self, format, *args) -> None:  # silencia el log de cada request
        pass


def iniciar_servidor() -> bool:
    """Arranca el servidor en un hilo daemon. Devuelve False si no se pudo (p. ej. puerto ocupado)."""
    global _servidor
    if _servidor is not None:
        return True
    crudo = os.getenv("UNITY_PORT", "8765")
    puerto = int(crudo) if crudo.isdigit() else 8765
    try:
        _servidor = ThreadingHTTPServer(("0.0.0.0", puerto), _Manejador)
    except OSError as e:
        print(f"⚠ No se pudo abrir el servidor de eventos para Unity en el puerto {puerto} ({e}).")
        return False
    _servidor.daemon_threads = True
    threading.Thread(target=_servidor.serve_forever, name="eventos-unity", daemon=True).start()
    print(f"📡 Servidor para Unity en http://localhost:{puerto}  (GET /eventos?desde=0, GET /estado)")
    return True


def servidor_activo() -> bool:
    return _servidor is not None

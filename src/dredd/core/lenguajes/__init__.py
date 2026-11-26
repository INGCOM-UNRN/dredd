"""Registro de adaptadores por lenguaje (`c`, `python`, `java` y plugins vía `dredd.languages`)."""

from dredd.core.lenguajes.base import (
    DIRECTORIOS_IGNORADOS,
    ENTRY_POINT_GROUP,
    AdaptadorLenguaje,
    archivos_proyecto_globales,
    detectar_lenguaje,
    es_ruta_ignorada,
    extensiones_fuente_globales,
    extensiones_ingesta_globales,
    listar_adaptadores,
    normalizar_hallazgo_yutani,
    normalizar_lenguaje,
    resolver_adaptador,
)
from dredd.core.lenguajes.c import AdaptadorC
from dredd.core.lenguajes.java import AdaptadorJava
from dredd.core.lenguajes.python import AdaptadorPython

__all__ = [
    "AdaptadorC",
    "AdaptadorJava",
    "AdaptadorLenguaje",
    "AdaptadorPython",
    "DIRECTORIOS_IGNORADOS",
    "ENTRY_POINT_GROUP",
    "archivos_proyecto_globales",
    "detectar_lenguaje",
    "es_ruta_ignorada",
    "extensiones_fuente_globales",
    "extensiones_ingesta_globales",
    "listar_adaptadores",
    "normalizar_hallazgo_yutani",
    "normalizar_lenguaje",
    "resolver_adaptador",
]

"""Lo que comparten los logs de los programas que corren solos (el servicio
de stock y la API del celular): que ningún secreto termine escrito en un
archivo.

Un log de estos se copia al Escritorio y se manda (la evidencia del
blindaje, una captura de la consola), y una captura es un chat igual
(regla 4 de CLAUDE.md). Por eso el tapado va acá, en un solo lugar, y no
copiado en cada servicio.
"""

import logging
import re


class FormatterQueTapa(logging.Formatter):
    """Tapa secretos en el texto FINAL (mensaje + traceback).

    Va en el Formatter y no en un logging.Filter a propósito: el traceback
    lo arma el Formatter DESPUÉS de los filtros, y con un filtro el token
    del bot aparecía entero en la traza de una excepción (verificado).
    """

    PATRONES = (
        re.compile(r"bot\d+:[\w-]+"),            # URL de Telegram con el token del bot
        re.compile(r"\b\d{5,}:[\w-]{8,}"),        # el token del bot suelto
        re.compile(r"Bearer \S+"),                # encabezado Authorization
        re.compile(r"v2\.[\w-]+\.[\w-]+"),        # token de sesión de la app
    )

    def format(self, record):
        texto = super().format(record)
        for patron in self.PATRONES:
            texto = patron.sub("[TAPADO]", texto)
        return texto

"""Cliente de Facturación Electrónica ARCA (ex AFIP) — WSAA + WSFEv1.

Toda integración con ARCA se hace en dos pasos:

1. WSAA (autenticación): se arma un "Ticket de Requerimiento de Acceso"
   (TRA), se firma con el certificado digital del comercio como CMS/PKCS#7
   (con el paquete `cryptography` — no hace falta tener `openssl.exe`
   instalado en la PC) y se manda al método `loginCms`. Devuelve un
   Token+Sign válidos por 12 horas, que se cachean en memoria mientras
   dure el proceso para no pedir uno nuevo en cada venta.
2. WSFEv1 (facturación): con ese Token+Sign se consulta el próximo número
   de comprobante disponible (FECompUltimoAutorizado) y se solicita el
   CAE (FECAESolicitar) — el código que hace válida la factura.

IMPORTANTE — esto requiere que el comercio ya tenga hecho, del lado de
ARCA (no es algo que este programa pueda resolver):
  - CUIT con Clave Fiscal y el servicio de Facturación Electrónica
    adherido.
  - Punto de venta dado de alta como "Electrónico".
  - Un certificado digital (.crt/.pem) y su clave privada (.key/.pem)
    generados desde el portal de ARCA, cargados en el Panel del Dueño
    (pestaña "Facturación ARCA").
Sin eso, `facturar_venta()` lanza ArcaError con un mensaje pensado para
mostrarle al cajero — nunca bloquea ni deshace el cobro en sí (ver
pos_core/sales.py: la venta ya quedó registrada antes de intentar
facturarla).

Nota sobre las URLs y el formato exacto del XML: siguen la documentación
pública de WSAA/WSFEv1 vigente al momento de escribir este módulo. ARCA
viene migrando de dominio (afip.gov.ar -> arca.gob.ar) y puede seguir
ajustando endpoints — antes de la primera factura real conviene
confirmar las URLs actuales en el portal de ARCA y probar a fondo contra
Homologación con el certificado de prueba.
"""

import base64
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

TIMEOUT_SEGUNDOS = 20

_URLS = {
    "homologacion": {
        "wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    },
    "produccion": {
        "wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
    },
}

_TIPO_CBTE = {"B": 6, "C": 11}  # códigos de comprobante que espera WSFEv1
_DOC_TIPO_CONSUMIDOR_FINAL = 99

_cache_ticket = {}  # ambiente -> {"token", "sign", "expiration": datetime}


class ArcaError(Exception):
    """Cualquier problema facturando: configuración incompleta, ARCA
    caído/inalcanzable, o comprobante rechazado. El mensaje siempre está
    pensado para mostrarse tal cual al cajero o al dueño."""


def _config_arca() -> dict:
    from pos_core.config import cargar_config
    cfg = cargar_config()
    if "arca" not in cfg:
        raise ArcaError("La facturación con ARCA no está configurada todavía.")
    return dict(cfg["arca"])


def _validar_config(cfg: dict) -> None:
    faltantes = []
    for clave, etiqueta in [("cuit", "CUIT"), ("punto_venta", "Punto de venta"),
                             ("certificado_path", "Certificado digital"),
                             ("clave_privada_path", "Clave privada")]:
        if not cfg.get(clave):
            faltantes.append(etiqueta)
    if faltantes:
        raise ArcaError("Falta configurar en el Panel del Dueño (pestaña 'Facturación ARCA'): "
                         + ", ".join(faltantes))
    if not os.path.isfile(cfg["certificado_path"]):
        raise ArcaError(f"No se encontró el archivo de certificado: {cfg['certificado_path']}")
    if not os.path.isfile(cfg["clave_privada_path"]):
        raise ArcaError(f"No se encontró el archivo de clave privada: {cfg['clave_privada_path']}")


def _generar_tra() -> str:
    ahora = datetime.now(timezone.utc).astimezone()
    generation = ahora - timedelta(minutes=10)
    expiration = ahora + timedelta(minutes=10)
    unique_id = str(int(time.time()))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        '<header>'
        f'<uniqueId>{unique_id}</uniqueId>'
        f'<generationTime>{generation.isoformat(timespec="seconds")}</generationTime>'
        f'<expirationTime>{expiration.isoformat(timespec="seconds")}</expirationTime>'
        '</header>'
        '<service>wsfe</service>'
        '</loginTicketRequest>'
    )


def _firmar_tra(tra_xml: str, certificado_path: str, clave_privada_path: str) -> str:
    """Firma el TRA como CMS/PKCS#7 (equivalente a `openssl smime -sign
    -nodetach`) y lo devuelve en base64, listo para mandar como `in0` al
    método loginCms de WSAA."""
    with open(certificado_path, "rb") as f:
        certificado = x509.load_pem_x509_certificate(f.read())
    with open(clave_privada_path, "rb") as f:
        clave_privada = serialization.load_pem_private_key(f.read(), password=None)

    firmado = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml.encode("utf-8"))
        .add_signer(certificado, clave_privada, hashes.SHA256())
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return base64.b64encode(firmado).decode("ascii")


def _sin_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _buscar(elemento: ET.Element, nombre: str):
    """Busca un tag por nombre local en cualquier profundidad, ignorando
    el namespace — las respuestas SOAP de ARCA cambian de namespace entre
    WSAA y WSFEv1, más simple normalizar así que declarar cada uno."""
    if _sin_ns(elemento.tag) == nombre:
        return elemento
    for hijo in elemento.iter():
        if _sin_ns(hijo.tag) == nombre:
            return hijo
    return None


def obtener_ticket_acceso(cfg: dict, *, forzar_nuevo: bool = False) -> dict:
    ambiente = cfg.get("ambiente", "homologacion")
    cacheado = _cache_ticket.get(ambiente)
    if not forzar_nuevo and cacheado and cacheado["expiration"] > datetime.now(timezone.utc):
        return cacheado

    tra_xml = _generar_tra()
    cms_b64 = _firmar_tra(tra_xml, cfg["certificado_path"], cfg["clave_privada_path"])

    sobre = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.desarrollo.afip.gov">'
        '<soapenv:Header/><soapenv:Body><wsaa:loginCms><wsaa:in0>'
        f'{cms_b64}'
        '</wsaa:in0></wsaa:loginCms></soapenv:Body></soapenv:Envelope>'
    )
    try:
        resp = requests.post(_URLS[ambiente]["wsaa"], data=sobre.encode("utf-8"),
                              headers={"Content-Type": "text/xml; charset=utf-8"},
                              timeout=TIMEOUT_SEGUNDOS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ArcaError(f"No se pudo conectar con ARCA (WSAA): {e}")

    raiz = ET.fromstring(resp.content)
    nodo_return = _buscar(raiz, "loginCmsReturn")
    if nodo_return is None or not nodo_return.text:
        raise ArcaError(f"WSAA no devolvió credenciales. Respuesta: {resp.text[:500]}")

    interior = ET.fromstring(nodo_return.text)
    token = _buscar(interior, "token")
    sign = _buscar(interior, "sign")
    expiration_txt = _buscar(interior, "expirationTime")
    if token is None or sign is None:
        raise ArcaError(f"WSAA no devolvió token/sign válidos. Respuesta: {resp.text[:500]}")

    try:
        expiration = datetime.fromisoformat(expiration_txt.text) if expiration_txt is not None else None
    except ValueError:
        expiration = None
    if expiration is None:
        expiration = datetime.now(timezone.utc) + timedelta(hours=11)

    ticket = {"token": token.text, "sign": sign.text, "expiration": expiration}
    _cache_ticket[ambiente] = ticket
    return ticket


def _llamar_wsfe(ambiente: str, soap_action: str, cuerpo_xml: str) -> ET.Element:
    sobre = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body>{cuerpo_xml}</soap:Body></soap:Envelope>'
    )
    try:
        resp = requests.post(
            _URLS[ambiente]["wsfe"], data=sobre.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": soap_action},
            timeout=TIMEOUT_SEGUNDOS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ArcaError(f"No se pudo conectar con ARCA (WSFEv1): {e}")
    return ET.fromstring(resp.content)


def consultar_ultimo_comprobante(cfg: dict, ticket: dict, cbte_tipo: int) -> int:
    cuerpo = (
        '<FECompUltimoAutorizado xmlns="http://ar.gov.afip.dif.FEV1/">'
        f'<Auth><Token>{ticket["token"]}</Token><Sign>{ticket["sign"]}</Sign>'
        f'<Cuit>{cfg["cuit"]}</Cuit></Auth>'
        f'<PtoVta>{cfg["punto_venta"]}</PtoVta><CbteTipo>{cbte_tipo}</CbteTipo>'
        '</FECompUltimoAutorizado>'
    )
    raiz = _llamar_wsfe(cfg.get("ambiente", "homologacion"),
                         "http://ar.gov.afip.dif.FEV1/FECompUltimoAutorizado", cuerpo)
    nodo = _buscar(raiz, "CbteNro")
    if nodo is None or nodo.text is None:
        raise ArcaError(f"ARCA no informó el último comprobante autorizado. Respuesta: "
                         f"{ET.tostring(raiz, encoding='unicode')[:500]}")
    return int(nodo.text)


def solicitar_cae(cfg: dict, ticket: dict, *, cbte_tipo: int, numero: int,
                   fecha: str, importe_total: float) -> dict:
    cuerpo = (
        '<FECAESolicitar xmlns="http://ar.gov.afip.dif.FEV1/">'
        f'<Auth><Token>{ticket["token"]}</Token><Sign>{ticket["sign"]}</Sign>'
        f'<Cuit>{cfg["cuit"]}</Cuit></Auth>'
        '<FeCAEReq>'
        f'<FeCabReq><CantReg>1</CantReg><PtoVta>{cfg["punto_venta"]}</PtoVta>'
        f'<CbteTipo>{cbte_tipo}</CbteTipo></FeCabReq>'
        '<FeDetReq><FECAEDetRequest>'
        f'<Concepto>1</Concepto><DocTipo>{_DOC_TIPO_CONSUMIDOR_FINAL}</DocTipo><DocNro>0</DocNro>'
        f'<CbteDesde>{numero}</CbteDesde><CbteHasta>{numero}</CbteHasta>'
        f'<CbteFch>{fecha}</CbteFch>'
        f'<ImpTotal>{importe_total:.2f}</ImpTotal><ImpTotConc>0.00</ImpTotConc>'
        f'<ImpNeto>{importe_total:.2f}</ImpNeto><ImpOpEx>0.00</ImpOpEx>'
        '<ImpIVA>0.00</ImpIVA><ImpTrib>0.00</ImpTrib>'
        '<MonId>PES</MonId><MonCotiz>1</MonCotiz>'
        '</FECAEDetRequest></FeDetReq></FeCAEReq>'
        '</FECAESolicitar>'
    )
    raiz = _llamar_wsfe(cfg.get("ambiente", "homologacion"),
                         "http://ar.gov.afip.dif.FEV1/FECAESolicitar", cuerpo)

    resultado = _buscar(raiz, "Resultado")
    if resultado is not None and resultado.text == "R":
        obs = _buscar(raiz, "Msg")
        raise ArcaError(f"ARCA rechazó el comprobante: {obs.text if obs is not None else 'sin detalle'}")

    cae = _buscar(raiz, "CAE")
    vencimiento = _buscar(raiz, "CAEFchVto")
    if cae is None or not cae.text:
        raise ArcaError(f"ARCA no devolvió un CAE. Respuesta: {ET.tostring(raiz, encoding='unicode')[:500]}")

    return {
        "cae": cae.text,
        "cae_vencimiento": vencimiento.text if vencimiento is not None else None,
        "numero_comprobante": numero,
    }


def facturar_venta(venta: dict) -> dict:
    """Punto de entrada único: dado un dict con al menos {total,
    fecha_hora}, gestiona todo el flujo (ticket de acceso, número de
    comprobante, CAE) y devuelve {tipo_comprobante, numero_comprobante,
    cae, cae_vencimiento}.

    Lanza ArcaError con un mensaje apto para mostrar al cajero/dueño si
    algo falla en cualquier paso."""
    cfg = _config_arca()
    if cfg.get("habilitado", "false").strip().lower() not in ("true", "1", "si", "sí"):
        raise ArcaError("La facturación con ARCA está deshabilitada (activarla en el Panel del "
                         "Dueño, pestaña 'Facturación ARCA').")
    _validar_config(cfg)

    tipo = cfg.get("tipo_comprobante", "B").strip().upper()
    if tipo not in _TIPO_CBTE:
        raise ArcaError(f"Tipo de comprobante configurado inválido: {tipo!r} (debe ser B o C).")
    cbte_tipo = _TIPO_CBTE[tipo]

    ticket = obtener_ticket_acceso(cfg)
    ultimo = consultar_ultimo_comprobante(cfg, ticket, cbte_tipo)
    numero = ultimo + 1

    fecha_cbte = venta["fecha_hora"][:10].replace("-", "")  # 'YYYY-MM-DD' -> 'YYYYMMDD'

    resultado = solicitar_cae(cfg, ticket, cbte_tipo=cbte_tipo, numero=numero,
                               fecha=fecha_cbte, importe_total=venta["total"])
    resultado["tipo_comprobante"] = tipo
    return resultado

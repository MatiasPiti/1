# Panel Dueño — app Android

El **Panel del Dueño** de la PC, en el celular de Leo: ventas del día, stock (con lector de código
de barras por cámara), precios, facturas PDF y alertas. Control total del negocio desde el
bolsillo.

```
 Celular de Leo                         PC del local (fuente de verdad)
┌──────────────────┐   Tailscale       ┌──────────────────────────────────────────┐
│  App Panel Dueño │ ═════════════════▶│ ApiDueno.exe ─▶ pos_core ─▶ stock.db       │
│  (Flutter)       │   (cifrado,       │ (services/api_dueno.py)   (la MISMA base   │
└──────────────────┘    privado)       │                            de MaestroDueno)│
                                       └──────────────────────────────────────────┘
```

La app **no tiene datos propios**: todo lo lee y escribe la PC del local a través de la API, con la
misma lógica que usa el panel de escritorio (`pos_core`). Si el celular hace un movimiento de
stock, queda en la auditoría como `dueño (app)`.

> Requisito: la PC del local tiene que estar **prendida y con internet** para usar la app.

---

## 1. En la PC del local (una sola vez)

### 1.1 Compilar y copiar la API

```bat
build\build_all.bat
```

Copiar `dist\ApiDueno\` completo a `C:\SistemaDual\ApiDueno\`.

### 1.2 Probarla a mano

```bat
C:\SistemaDual\ApiDueno\ApiDueno.exe --base "C:\SistemaDual\MaestroDueno"
```

`--base` **tiene que apuntar a la carpeta de MaestroDueno** (la que tiene `database\stock.db` y
`config.ini`): así la app del celular ve exactamente los mismos datos que el panel de la PC.

Abrir en el navegador de la PC `http://localhost:8765/api/salud`. Tiene que responder algo como
`{"ok": true, "nombre_local": "Mi Negocio", ...}`.

- El nombre que muestra la app sale de `config.ini` → `[general] nombre_local = ...`.
- El PIN es el del usuario **dueño** del sistema (el que se definió con `scripts\setup_inicial.py`).

### 1.3 Que arranque sola con Windows

En una consola **como administrador**:

```bat
schtasks /Create /TN "SistemaDual ApiDueno" /SC ONSTART /RU SYSTEM /RL HIGHEST /F ^
  /TR "\"C:\SistemaDual\ApiDueno\ApiDueno.exe\" --base \"C:\SistemaDual\MaestroDueno\""
schtasks /Run /TN "SistemaDual ApiDueno"
```

Corre sin ventana; su registro queda en `C:\SistemaDual\MaestroDueno\logs\api_dueno.log`.

### 1.4 Firewall: abrir el puerto solo para Tailscale

```bat
netsh advfirewall firewall add rule name="ApiDueno (Tailscale)" dir=in action=allow ^
  protocol=TCP localport=8765 remoteip=100.64.0.0/10
```

`100.64.0.0/10` es el rango de direcciones de Tailscale: la API queda accesible **solo** desde los
dispositivos de tu red Tailscale, no desde el Wi-Fi del local ni desde internet.

---

## 2. Tailscale (la conexión segura)

Tailscale crea una red privada y cifrada entre la PC y el celular, sin abrir puertos del router ni
contratar nada. Es gratis para este uso.

1. Crear una cuenta en <https://tailscale.com> (por ejemplo con la cuenta de Google de Leo).
2. **En la PC:** instalar Tailscale para Windows e iniciar sesión con esa cuenta. En el menú de
   Tailscale activar **"Run unattended"**, para que funcione aunque nadie haya iniciado sesión en
   Windows.
3. **En el celular:** instalar Tailscale desde Play Store e iniciar sesión con **la misma cuenta**.
4. En la consola de administración (<https://login.tailscale.com/admin/machines>), en la PC:
   menú `…` → **Disable key expiry**. Si no, cada 180 días hay que volver a loguear la PC.
5. Anotar la dirección de la PC: aparece en la app de Tailscale como `100.x.y.z` o con su nombre
   (por ejemplo `pc-local`). Cualquiera de las dos sirve.

---

## 3. Instalar la app en el celular

1. Descargar `panel-dueno.apk`:
   - de la pestaña **Actions** del repo → la última corrida de **"Panel Dueño"** → *Artifacts* →
     `panel-dueno-apk` (viene en un .zip), o
   - de **Releases**, si se publicó una versión (ver sección 6).
2. Abrir el `.apk` en el celular. La primera vez Android pide permitir *"instalar apps de origen
   desconocido"* para el navegador o el gestor de archivos: aceptar.
3. Abrir **Panel Dueño**:
   - **Dirección de la PC:** la de Tailscale (`100.x.y.z` o `pc-local`). Tocar **Probar**: tiene
     que decir *Conectado a "Nombre del local"*.
   - **PIN de dueño** → **Ingresar**.
   - Aceptar **entrar con huella o rostro**.

Listo. Las próximas veces se abre con la huella.

---

## 4. Qué se puede hacer

| Pestaña | Qué hace |
|---|---|
| **Inicio** | Ventas de hoy, tickets, ticket promedio, medios de pago, gráfico de los últimos 7 días y productos más vendidos (hoy / 7 días / 30 días / siempre). Se actualiza solo cada minuto. |
| **Stock** | Buscar por código o nombre (o escanear), sumar/restar con cantidad y motivo, últimos movimientos. **Modo lector**: escaneás productos uno tras otro y cada lectura resta 1 unidad, igual que el lector USB de la PC (o suma 1, para cuando entra mercadería). |
| **Precios** | Filtrar por código/nombre, marca, proveedor o categoría. Tocar un producto para ponerle precio exacto (muestra el margen). **Ajuste masivo** por % o $ fijo, con redondeo a la centena superior y **vista previa obligatoria** antes de aplicar. |
| **Facturas** | Elegir un PDF del proveedor (WhatsApp, mail, Drive). La PC lo lee, vos revisás y corregís códigos (escaneando) y cantidades, y recién ahí se suma al stock. |
| **Alertas** | Productos con stock bajo o sobre-stock (con globito en la pestaña), reposición rápida, configuración de Telegram y del umbral global. |
| **Ajustes** ⚙️ | Tema **claro** u **oscuro elegante**, huella/rostro, datos de conexión, cerrar sesión. |

En **todos los campos de código** hay un botón 📷 para escanear el código de barras con la cámara.

> Las facturas tienen que ser **PDF con texto**. Una foto de una factura de papel no se puede leer
> (tampoco en la PC): esos productos se cargan desde **Stock**.

---

## 5. Seguridad

- El **PIN nunca se guarda en el celular**: lo valida la PC, que devuelve una sesión firmada que
  dura 30 días y se guarda en el llavero cifrado de Android.
- **5 PIN incorrectos** seguidos bloquean los intentos por 5 minutos.
- La app se **bloquea sola** después de 2 minutos en segundo plano (pide huella o PIN).
- Con la regla de firewall de 1.4, la API solo es alcanzable desde la red Tailscale.

**Si se pierde el celular:**
1. Quitarlo de la red en <https://login.tailscale.com/admin/machines> (deja de poder conectarse).
2. Invalidar todas las sesiones: en `C:\SistemaDual\MaestroDueno\config.ini` borrar la línea
   `secreto = ...` de la sección `[api]` y reiniciar la tarea `SistemaDual ApiDueno`.

---

## 6. Firma del APK (para actualizar sin desinstalar)

Android solo deja instalar una versión nueva **encima** de la anterior si las dos están firmadas
con la misma clave. Sin clave propia, el CI firma con una clave descartable y para actualizar hay
que desinstalar (y volver a ingresar el PIN).

Para usar una clave fija, una sola vez:

```bash
keytool -genkey -v -keystore panel-dueno.jks -keyalg RSA -keysize 2048 -validity 10000 -alias panel-dueno
```

Pasarla a base64 (Linux/Mac: `base64 -w0 panel-dueno.jks`; Windows PowerShell:
`[Convert]::ToBase64String([IO.File]::ReadAllBytes("panel-dueno.jks"))`) y cargar en GitHub →
*Settings → Secrets and variables → Actions* estos 4 secrets:

| Secret | Valor |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | el texto base64 del `.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | la contraseña del keystore |
| `ANDROID_KEY_ALIAS` | `panel-dueno` |
| `ANDROID_KEY_PASSWORD` | la contraseña de la clave |

**Guardá el `.jks` y las contraseñas en un lugar seguro**: si se pierden, no se puede actualizar
la app instalada sin desinstalarla.

Para publicar una versión descargable desde *Releases*: subir `version:` en `pubspec.yaml`
(por ejemplo `1.0.1+2`) y pushear un tag `movil-v1.0.1`.

---

## 7. Desarrollo

```bash
# App (requiere Flutter 3.47+)
cd apps/movil_dueno
flutter pub get
flutter analyze
flutter test                      # todos los tests
flutter test test/sesion_test.dart --plain-name "bloquea"   # uno solo
flutter run                       # en un celular conectado por USB
flutter build apk --release

# API (desde la raíz del repo)
python -m pytest -q
python services/api_dueno.py --base apps/master_dueno      # usa la base de dev de MaestroDueno
```

Estructura de `lib/`:

- `api/` — cliente HTTP (`cliente_api.dart`) y modelos; mismo contrato que `services/api_dueno.py`.
- `estado.dart` — sesión (login, token, bloqueo/huella), tema elegido y contador de alertas.
- `pantallas/` — una por pestaña, más login, bloqueo, modo lector y ajustes.
- `widgets/` — `CampoCodigo` (campo con botón de cámara, usado en todos lados) y el escáner.
- `tema.dart` — paleta del logo y temas claro/oscuro.

---

## Problemas frecuentes

| Mensaje | Qué revisar |
|---|---|
| *No se pudo conectar con la PC del local* | ¿La PC está prendida y con internet? ¿Tailscale está conectado en los dos equipos? En la PC, ¿responde `http://localhost:8765/api/salud`? ¿Está la regla de firewall? |
| *Sesión vencida o inválida* | Pasaron 30 días o se reseteó el `secreto`: ingresar el PIN de nuevo. |
| *Demasiados intentos* | Se equivocó el PIN 5 veces: esperar 5 minutos. |
| *La app no tiene permiso para usar la cámara* | Ajustes del teléfono → Apps → Panel Dueño → Permisos → Cámara. |
| *Este PDF parece una imagen escaneada* | El PDF no tiene texto: cargar esos productos desde Stock. |

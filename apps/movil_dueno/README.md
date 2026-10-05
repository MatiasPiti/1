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

> **Importante: qué datos ve la app.** La app muestra la base a la que apunta `--base` (la de
> **MaestroDueno**). Con el despliegue actual cada programa usa la carpeta `database\` de **su propia
> carpeta**, así que **las ventas que cobra MaestroCaja (y el stock que descuentan) quedan en
> `C:\SistemaDual\MaestroCaja\database\stock.db` y no aparecen en la app** (tampoco en el panel de la
> PC) hasta que se unifiquen las bases, algo que está pendiente. Lo que se hace desde MaestroDueno o
> desde la app (stock, precios, facturas, alertas) sí se ve en los dos.

---

## 1. En la PC del local (una sola vez)

### 1.1 Compilar y copiar la API

En una PC con Windows y Python, desde la carpeta del repo (en la rama
`claude/dual-pos-portable-emergency-dvt5ym`, que es la que trae la API):

```bat
python -m pip install -r requirements.txt
build\build_all.bat
```

Queda en **`dist\ApiDueno\ApiDueno.exe`** (cada programa en su propia subcarpeta de `dist`). Copiar
la carpeta `dist\ApiDueno\` **completa** (no solo el `.exe`) a `C:\SistemaDual\ApiDueno\`.

- `build_all.bat` usa `python -m PyInstaller`, así que anda aunque pip avise que la carpeta
  `Scripts` de Python *"is not on PATH"*. Si un programa no compila, al final dice cuál.
- Para compilar **solo** la API (una sola línea, sirve en `cmd` y en PowerShell):
  `python -m PyInstaller --noconfirm --clean --onedir --noconsole --add-data "sql\schema.sql;sql" --name ApiDueno --paths . --collect-submodules uvicorn --collect-data pdfminer --exclude-module pandas --exclude-module scipy --exclude-module matplotlib --exclude-module tkinter --exclude-module numpy --exclude-module sqlalchemy --exclude-module pytest services\api_dueno.py`
  (los `--exclude-module` dejan afuera librerías pesadas que la API no usa, como pandas o scipy: la carpeta queda en unos 80 MB).

### 1.2 Definir el PIN del dueño y probarla a mano

La app entra con el PIN del usuario **dueño** guardado en la base de MaestroDueno. En la PC del
local no hace falta Python: el PIN se define con el mismo `ApiDueno.exe`. En una consola `cmd`:

```bat
start "" /wait C:\SistemaDual\ApiDueno\ApiDueno.exe --base "C:\SistemaDual\MaestroDueno" --definir-pin 1234
type C:\SistemaDual\MaestroDueno\logs\api_dueno.log
```

- Cambiá `1234` por el PIN que quieras: de **4 a 12 dígitos**, solo números.
- `--definir-pin` crea el usuario `dueño` (rol DUEÑO) o, si ya existe, le cambia el PIN y lo deja
  activo; después **sale sin levantar la API**. Si todavía no existe `database\stock.db`, la crea.
  Si el PIN no tiene ese formato o la carpeta de `--base` no existe, no toca nada y lo anota en el log
  (si la carpeta no existe, el log queda en `C:\SistemaDual\ApiDueno\logs\api_dueno.log`).
- ApiDueno no tiene ventana ni escribe en la consola: el resultado queda en `logs\api_dueno.log`
  (*"Usuario 'dueño' (rol DUEÑO) creado..."* o *"PIN del usuario 'dueño' actualizado..."*; el PIN
  nunca se anota). `start "" /wait` hace que la consola espere a que termine antes del `type`.
- Sirve también para **cambiar el PIN** más adelante; ojo, eso cierra la sesión de los celulares
  (ver sección 5).

En desarrollo, desde la raíz del repo: `python scripts/setup_inicial.py --base apps/master_dueno`
(crea la base y pide el PIN) o `python services/api_dueno.py --base apps/master_dueno --definir-pin 1234`.

Después, probar la API escuchando **solo en la propia PC**:

```bat
C:\SistemaDual\ApiDueno\ApiDueno.exe --base "C:\SistemaDual\MaestroDueno" --host 127.0.0.1
```

- `--base` **tiene que apuntar a la carpeta de MaestroDueno** (la que tiene `database\stock.db` y
  `config.ini`), sin `\` al final: así la app del celular ve los mismos datos que el panel de la PC.
- `--host 127.0.0.1` hace que no acepte conexiones de afuera, y así Windows no muestra el aviso del
  Firewall. Si ese aviso aparece y se acepta, abre la API a toda la red del local (ver 1.4).

Abrir en el navegador de la PC `http://127.0.0.1:8765/api/salud`. Tiene que responder algo como
`{"ok": true, "nombre_local": "Mi Negocio", ...}`. El nombre que muestra la app sale de `config.ini`
→ `[general] nombre_local = ...`.

**Si no responde**, mirar `C:\SistemaDual\MaestroDueno\logs\api_dueno.log` (si la carpeta de `--base`
no existe, el log queda en `C:\SistemaDual\ApiDueno\logs\api_dueno.log`). La API **no arranca**, y
anota el motivo en el log, si:

- `--base` está mal: no existe `<carpeta de --base>\database\stock.db`;
- en esa base no hay ningún usuario DUEÑO activo (falta el `--definir-pin` de arriba);
- `config.ini` existe pero no se puede leer.

Al terminar la prueba, **cerrarla antes de seguir con 1.3**. Como no tiene ventana, sigue corriendo
de fondo aunque cierres la consola:

```bat
taskkill /IM ApiDueno.exe /F
```

### 1.3 Que arranque sola con Windows

En **PowerShell como administrador** (menú Inicio → escribir *PowerShell* → clic derecho →
*Ejecutar como administrador*):

```powershell
$accion = New-ScheduledTaskAction -Execute 'C:\SistemaDual\ApiDueno\ApiDueno.exe' `
    -Argument '--base "C:\SistemaDual\MaestroDueno"' -WorkingDirectory 'C:\SistemaDual\ApiDueno'
$inicio = New-ScheduledTaskTrigger -AtStartup
$ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'SistemaDual ApiDueno' -Action $accion -Trigger $inicio `
    -Settings $ajustes -User SYSTEM -RunLevel Highest -Force
Start-ScheduledTask -TaskName 'SistemaDual ApiDueno'
```

- `-ExecutionTimeLimit ([TimeSpan]::Zero)`: **sin límite de tiempo**. Por defecto Windows corta las
  tareas programadas a las 72 horas; por eso no se usa `schtasks /Create`, que deja ese límite y
  hacía que ApiDueno.exe muriera a los 3 días.
- `-RestartCount` / `-RestartInterval`: si la tarea falla, Windows la reintenta cada minuto.
- `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`: que no se corte si la PC es una notebook
  y se desenchufa.
- **Comillas:** el argumento va entre comillas **simples** (`'--base "C:\..."'`) para que la ruta
  le llegue a ApiDueno con sus comillas dobles. La ruta no lleva `\` al final: `\"` se toma como una
  comilla escapada y rompe el argumento.
- `-Force` reemplaza la tarea si ya existía (por ejemplo, una creada antes con `schtasks`).

Corre sin ventana; su registro queda en `C:\SistemaDual\MaestroDueno\logs\api_dueno.log`. Para
reiniciarla (por ejemplo, después de tocar `config.ini`):

```powershell
Stop-ScheduledTask -TaskName 'SistemaDual ApiDueno'; Start-ScheduledTask -TaskName 'SistemaDual ApiDueno'
```

### 1.4 Firewall: abrir el puerto solo para Tailscale

En una consola **`cmd` como administrador** (no en PowerShell: ahí no anda el `^` que corta la línea):

```bat
netsh advfirewall firewall delete rule name=all program="C:\SistemaDual\ApiDueno\ApiDueno.exe"
netsh advfirewall firewall add rule name="ApiDueno (Tailscale)" dir=in action=allow ^
  protocol=TCP localport=8765 remoteip=100.64.0.0/10
```

1. La primera línea borra las reglas que Windows crea **por programa** cuando aparece el aviso
   *"Firewall de Windows bloqueó algunas características de esta aplicación"* (por ejemplo, si se
   probó ApiDueno sin `--host 127.0.0.1`). Si el aviso se aceptó, esa regla deja entrar a la API
   **desde cualquier dirección** (el Wi-Fi del local incluido); si se canceló, crea una regla de
   **bloqueo**, que gana sobre la de abajo. Si responde que no hay reglas que coincidan, está bien:
   no había ninguna.
2. La segunda abre el puerto 8765 solo para `100.64.0.0/10`, el rango de direcciones de Tailscale:
   la API queda accesible **solo** desde los dispositivos de tu red Tailscale, no desde el Wi-Fi del
   local ni desde internet.

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
| **Alertas** | Productos con stock bajo o sobre-stock (con globito en la pestaña), reposición rápida, configuración de Telegram y del umbral global. Los avisos automáticos por Telegram los manda **ApiDueno** desde la PC (revisa cada 5 minutos; no repite el mismo aviso antes de 4 horas), así que llegan aunque la app esté cerrada. |
| **Ajustes** ⚙️ | Tema **claro** u **oscuro elegante**, huella/rostro, datos de conexión, cerrar sesión. |

En **todos los campos de código** hay un botón 📷 para escanear el código de barras con la cámara.

> Las facturas tienen que ser **PDF con texto**. Una foto de una factura de papel no se puede leer
> (tampoco en la PC): esos productos se cargan desde **Stock**.

---

## 5. Seguridad

- El **PIN nunca se guarda en el celular**: lo valida la PC, que devuelve una sesión firmada que
  dura 30 días y se guarda en el llavero cifrado de Android.
- **Cambiar el PIN del dueño** con `--definir-pin` (ver 1.2) **cierra la sesión de todos los
  celulares** en el acto, sin reiniciar nada: cada uno tiene que volver a entrar con el PIN nuevo.
  Lo mismo pasa si al usuario dueño se lo desactiva o se le saca el rol DUEÑO.
- **5 PIN incorrectos** seguidos desde un mismo dispositivo bloquean sus intentos por 5 minutos.
  Además hay un tope general: **más de 20 PIN incorrectos en 5 minutos**, sumando todos los
  dispositivos, bloquean el ingreso para todos durante 5 minutos.
- Las facturas PDF pueden pesar **hasta 20 MB**: la PC rechaza cualquier archivo más grande.
- La app se **bloquea sola** después de 2 minutos en segundo plano (pide huella o PIN).
- Con la regla de firewall de 1.4, la API solo es alcanzable desde la red Tailscale.

**Si se pierde el celular:**
1. Quitarlo de la red en <https://login.tailscale.com/admin/machines> (deja de poder conectarse).
2. Cambiar el PIN del dueño (1.2): las sesiones abiertas dejan de valer al instante y los otros
   celulares entran con el PIN nuevo.
3. Para invalidar todas las sesiones **sin** cambiar el PIN: en
   `C:\SistemaDual\MaestroDueno\config.ini` borrar la línea `secreto = ...` de la sección `[api]` y
   reiniciar la tarea `SistemaDual ApiDueno` (ver 1.3).

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

El CI escribe en `android/key.properties` solo la ruta del keystore y le pasa las contraseñas y el
alias a Gradle como variables de entorno con esos mismos nombres, así una contraseña con `\` (u
otros caracteres raros) no rompe la firma. Para firmar a mano en una PC: dejar el `.jks` en
`android/app/`, escribir `storeFile=panel-dueno.jks` en `android/key.properties` y definir esas tres
variables de entorno (o, si no, poner `storePassword`, `keyAlias` y `keyPassword` en el mismo
`key.properties`, donde cada `\` se escribe `\\`).

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
python scripts/setup_inicial.py --base apps/master_dueno   # crea la base de dev y pide el PIN del dueño
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
| *No se pudo conectar con la PC del local* | ¿La PC está prendida y con internet? ¿Tailscale está conectado en los dos equipos? En la PC, ¿responde `http://127.0.0.1:8765/api/salud`? Si no, ver las filas siguientes y `C:\SistemaDual\MaestroDueno\logs\api_dueno.log`. ¿Está la regla de firewall (1.4)? |
| En el log: *La API no arrancó: no existe la base de datos...* | `--base` está mal: tiene que ser la carpeta de MaestroDueno, sin `\` al final. Corregir la tarea (1.3). |
| En el log: *...no tiene ningún usuario DUEÑO activo* | Definir el PIN con `--definir-pin` (1.2) y reiniciar la tarea. |
| La API se corta sola a los 3 días | La tarea se creó con `schtasks`, que la limita a 72 horas: volver a crearla con PowerShell como en 1.3. |
| Apareció el aviso del Firewall de Windows, o la API responde desde el Wi-Fi del local | El aviso crea reglas por programa que pasan por encima de la de Tailscale. En `cmd` como administrador: `netsh advfirewall firewall delete rule name=all program="C:\SistemaDual\ApiDueno\ApiDueno.exe"` y dejar solo la regla de 1.4. |
| Las ventas de la caja no aparecen en la app | Con el despliegue actual MaestroCaja graba en la base de su propia carpeta (ver el aviso al principio): no es un error de la app. |
| *Sesión vencida o inválida* | Pasaron 30 días, se cambió el PIN del dueño o se reseteó el `secreto`: ingresar el PIN de nuevo. |
| *Demasiados intentos* | 5 PIN incorrectos desde el celular, o más de 20 en 5 minutos sumando todos: esperar 5 minutos. |
| *La app no tiene permiso para usar la cámara* | Ajustes del teléfono → Apps → Panel Dueño → Permisos → Cámara. |
| *Este PDF parece una imagen escaneada* | El PDF no tiene texto: cargar esos productos desde Stock. |

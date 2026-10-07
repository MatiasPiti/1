# Panel Dueño — app Android

App para el celular de Leo con lo principal del Panel del Dueño: dashboard, stock (con el lector de
códigos por cámara), precios con la cadena Costo S/IVA → Precio Costo → % Ganancia → Precio Final,
edición masiva, facturas PDF, umbrales y alertas.

Habla con **ApiCelular**, un servicio de Windows aparte que corre en la PC del local en el puerto
**8766** y usa el `pos_core` de Otter sobre la base real (`C:\SistemaDual\database\stock.db`).
El celular llega a la PC solo por **Tailscale**. El Dueño Remoto de la laptop (StockService, puerto
8765) no cambia en nada.

> **El ApiDueno viejo (`ApiDueno.exe`, de la rama `claude/dual-pos-portable-emergency-dvt5ym`) está
> descartado y no se usa nunca más.** Corrido contra la base real apagaba umbrales 5/0 con su propia
> migración y su monitor duplicaba las alertas de Telegram. Si queda alguna carpeta `ApiDueno` en la
> PC del local o en un pendrive, se borra.

**Siempre primero la PC y después el celular.** Si la PC tiene una versión anterior de la API, la
app lo dice al conectarse; no se rompe nada.

---

## 1. Compilar (en tu laptop con Windows)

```powershell
# --- Desde la carpeta del repo ---
git pull
py -3.12 -m venv venv
Set-ExecutionPolicy -Scope Process Bypass
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python tests\correr_todos.py
build\build_all.bat
robocopy dist E:\dist /E /MT:16
```

- `correr_todos.py` tiene que dar **todo en verde**. Si algo da rojo, se para acá.
- `build_all.bat` tiene que terminar diciendo **`ApiCelular: autoprueba OK`**: compila
  `ApiCelular.exe` y lo prueba solo, sobre una base temporal (nunca toca una base real).
- `robocopy` devuelve 0 a 3 cuando salió bien; 8 o más es error. Cambiá `E:` por la letra del pendrive.

## 2. Ensayo en tu laptop, antes de la visita

Lo que falle acá no se descubre en el local con el negocio esperando:

1. `OtterInstalador.exe` como administrador, en una carpeta nueva (por ejemplo `C:\OtterEnsayo`),
   tildando **"Instalar también la API del celular"**.
2. Definir un PIN de prueba con el acceso directo **"Otter - PIN del celular"** (menú Inicio → Otter),
   **sin** "Ejecutar como administrador".
3. Desde tu celular, con datos móviles y Tailscale prendido: instalar el APK (sección 4) y entrar
   contra la IP de Tailscale de la laptop.
4. `ApiCelular.exe deshabilitar` y `habilitar` (sección 6), y una prueba de reinicio **sin iniciar
   sesión** en Windows: desde el celular el dashboard tiene que cargar.
5. Al final, desinstalarla con el procedimiento de la sección 7 (así queda probado también).

## 3. En la PC del local

### 3.1 Antes de la visita: ACLs de Tailscale

Sin ACLs, el celular llegaría también al SSH de soporte, al 8765 y a los demás aparatos (regla 5).

1. En la consola web de Tailscale, **guardá la política actual** (copiá el texto del editor a un
   archivo de tu laptop) para poder volver atrás.
2. Escribí la política con sus `tests`. Tailscale **no guarda** una política cuyos tests fallan: si
   algo de lo que hoy anda (Dueño Remoto, Semáforo, SSH) quedara cortado, se ve ahí y no en el local.
   A la PC **no** se le pone etiqueta (le cambiaría el dueño); se le da un alias en `hosts`. Se
   etiqueta **solo el celular**. La IP real va solo en la consola de Tailscale, nunca en el repo ni
   en un chat:

   ```json
   {
     "tagOwners": {"tag:galpon-celular": ["autogroup:admin"]},
     "hosts":     {"galpon-pc": "100.X.Y.Z"},
     "acls": [
       {"action": "accept", "src": ["autogroup:member"],   "dst": ["*:*"]},
       {"action": "accept", "src": ["tag:galpon-celular"], "dst": ["galpon-pc:8766"]}
     ],
     "tests": [
       {"src": "tag:galpon-celular", "accept": ["galpon-pc:8766"], "deny": ["galpon-pc:22", "galpon-pc:8765"]},
       {"src": "<usuario de la laptop de Leo>", "accept": ["galpon-pc:8765"]},
       {"src": "<usuario de tus aparatos>", "accept": ["galpon-pc:22", "galpon-pc:8765", "galpon-pc:8766"]}
     ]
   }
   ```

   Si la política actual tiene otras secciones (`ssh`, `grants`, otros `tagOwners`), se conservan.

### 3.2 Mirar el firewall antes de instalar

Copiá la salida a tu laptop; **no** la mandes por chat si trae IPs:

```powershell
Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow |
    Where-Object { $_.DisplayName -like '*Tailscale*' -or $_.DisplayName -like '*Stock*' } |
    Format-Table DisplayName, Profile
Get-NetFirewallProfile | Format-Table Name, Enabled
```

Lo que importa es que no aparezca ninguna regla Allow sobre el 8766 o sobre ApiCelular sin limitar al
rango de Tailscale (la revisión final también lo mira).

### 3.3 Instalar con el Actualizador

1. Como administrador: `E:\dist\OtterActualizador\OtterActualizador.exe`, tildar **"Instalar también
   la API del celular (app del dueño, puerto 8766)"** y ACTUALIZAR. Si la API falla, se anota y la
   caja se actualiza igual.
2. La revisión final tiene que dar todo SI, salvo "PIN definido" y el watchdog (se arreglan en los
   pasos siguientes).
3. Botón **"Definir PIN del celular"**: Leo escribe su PIN (6 a 12 números) dos veces. Nadie mira,
   nadie lo anota, no va por chat ni en una captura. El PIN 1234 viejo ya no sirve.
4. **OtterBlindaje** con la casilla **«Solo actualizar el watchdog»** (no corta la red): crea la tarea
   `OtterWatchdogCelular`, que vigila la API cada 5 minutos.

### 3.4 Verificar

```powershell
Get-Service SistemaDualApiCelular | Select-Object Status, StartType
(Invoke-RestMethod http://127.0.0.1:8766/api/salud).servicio
& 'C:\SistemaDual\ApiCelular\ApiCelular.exe' diagnostico
Get-ScheduledTaskInfo OtterWatchdogCelular | Select-Object LastRunTime, LastTaskResult
```

Tiene que dar `Running` / `Automatic`, `otter-api-celular`, todo `[SI]` en el diagnóstico y
`LastTaskResult` 0 (a los 5 minutos).

## 4. En el celular de Leo

1. Instalar **Tailscale** e iniciar sesión. **Apenas aparece, etiquetarlo** `tag:galpon-celular` desde
   la consola web (*Machines* → el celular → *Edit ACL tags*).
2. Instalar el APK: GitHub → *Actions* → la última corrida de "Otter (pruebas + app del celular)" →
   *Artifacts* → `panel-dueno-apk` (o el Release `movil-v…` si se publicó). La primera vez Android
   pide permitir "instalar apps de origen desconocido".
3. En la app de Tailscale, mantener apretada la PC del local → *Copy IP* → pegarla en la app → **Probar
   conexión**. Tiene que mostrar **el nombre del negocio** y que la PC encontró la base.
4. Leo escribe su PIN y activa la huella.

**Prueba de la regla 1:** con datos móviles (Wi-Fi apagado) la app entra. Con el celular en el Wi-Fi
del local, `http://<IP de la PC en la red del local>:8766/api/salud` desde el navegador **no** muestra
datos (no carga, o dice `red_no_permitida`).

**Prueba de reinicio:** reiniciar la PC del local y **no** iniciar sesión. Desde el celular el
dashboard tiene que cargar.

## 5. Qué se puede hacer desde el celular

| Pantalla | Qué hace |
|---|---|
| Inicio | Ventas de hoy y de los últimos días, más vendidos, alertas activas, estado del bot de Telegram. |
| Stock | Buscar por nombre o código (con la cámara), ver el detalle con el precio que cobra la Caja (ofertas incluidas) y los últimos movimientos, sumar o restar stock. Avisa si hay ventas que todavía falta descontar de ese producto. |
| Modo lector | Escanear de corrido con la cámara: cada lectura suma o resta 1, como el lector del Panel. |
| Precios | La cadena de 4 valores: la recalcula la PC igual que la pantalla de Precios del Panel. Si algo cambió mientras estaba abierta (una factura, otra persona), avisa y recarga. Edición masiva con vista previa idéntica a lo que se aplica. |
| Facturas | Subir un PDF, revisar renglón por renglón y aplicar. Lo emparejado por nombre nunca se tilda solo. Si la misma factura ya se cargó hace poco, pregunta antes de sumar de nuevo. |
| Alertas | Productos que cruzaron su umbral, umbral global (0 = no avisar), quitar el umbral global, cuántos productos tienen umbral propio, prender/apagar el bot y mandar un mensaje de prueba. |

**No** se puede desde el celular (a propósito): cargar o ver el token del bot de Telegram (se carga en
el Panel de la PC), "Quitar TODOS los umbrales propios", Excel, ARCA, alta de productos ni ofertas.

## 6. Operación

- **Leo olvidó el PIN:** en la PC del local, menú Inicio → Otter → **"Otter - PIN del celular"**. No hace
  falta administrador ni Matías.
- **Apagarla a propósito:** solo así (deja de escuchar en 30 segundos; para volver, `habilitar`):

  ```powershell
  & 'C:\SistemaDual\ApiCelular\ApiCelular.exe' deshabilitar
  ```

  **No** editar config.ini a mano (un comentario al final de la línea o un Bloc de notas que lo guarde
  con BOM rompen la lectura, y con BOM no arranca ni la Caja) y **no** usar
  `sc.exe config ... start= disabled`.
- **Soporte por SSH** (`otter_soporte`):

  ```powershell
  Get-Service SistemaDualApiCelular
  & 'C:\SistemaDual\ApiCelular\ApiCelular.exe' diagnostico
  Get-Content C:\SistemaDual\logs\api_celular.log -Tail 50
  Get-Content C:\SistemaDual\watchdog\watchdog_celular.log -Tail 20
  ```

  Para reiniciarla: `sc.exe stop SistemaDualApiCelular` y después `sc.exe start SistemaDualApiCelular`.
  **No** `Restart-Service` (si se traba, deja la sesión colgada). **Nunca `definir-pin` por SSH.**
- **Celular perdido o robado, en este orden:**
  1. Sacarlo de la tailnet desde la consola web de Tailscale (*Machines* → el celular → *Remove*).
  2. Por SSH: `& 'C:\SistemaDual\ApiCelular\ApiCelular.exe' cerrar-sesiones` (ningún celular queda
     con sesión abierta).
  3. Si el PIN pudo quedar a la vista, Leo define uno nuevo con "Otter - PIN del celular".

## 7. Desinstalar la API del celular

Como administrador (sirve por SSH). El orden importa: primero se apaga y se saca la tarea, así el
watchdog no la vuelve a levantar en el medio.

```powershell
& 'C:\SistemaDual\ApiCelular\ApiCelular.exe' deshabilitar
Unregister-ScheduledTask -TaskName OtterWatchdogCelular -Confirm:$false -ErrorAction SilentlyContinue
& 'C:\SistemaDual\ApiCelular\ApiCelular.exe' stop
& 'C:\SistemaDual\ApiCelular\ApiCelular.exe' remove
Remove-NetFirewallRule -Name OtterApiCelular-Tailscale -ErrorAction SilentlyContinue
Remove-Item 'C:\SistemaDual\ApiCelular' -Recurse -Force
Remove-Item 'C:\SistemaDual\watchdog\watchdog_celular.ps1' -ErrorAction SilentlyContinue
Remove-Item "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Otter\Otter - PIN del celular.lnk" -ErrorAction SilentlyContinue
```

- Si `stop` deja el servicio en "Stop Pending", esperar o cerrar el proceso (`Stop-Process -Force` sobre
  el PID que da `sc.exe queryex SistemaDualApiCelular`) **antes** de `remove`: con el proceso vivo,
  `remove` lo deja "marcado para borrar" hasta reiniciar.
- La sección `[api_celular]` queda con `habilitado = false` y `C:\SistemaDual\api_celular\secreto.json`
  puede quedar: sin el servicio no los usa nadie.
- Volver a instalarla es el Actualizador con la casilla.

## 8. Problemas frecuentes

| Lo que dice la app | Qué es |
|---|---|
| "Ese es el puerto del Dueño Remoto" | Se puso el 8765. La API del celular está en el 8766 (la app lo completa sola si no se pone puerto). |
| "Contesta otro programa" | Otro proceso tiene el 8766: `diagnostico` dice cuál. |
| PIN no definido | Falta definir el PIN, o el que había era el viejo. |
| La PC no encuentra la base | ApiCelular quedó en otra carpeta que no es `C:\SistemaDual\ApiCelular`. |
| Base desactualizada | Falta correr el Actualizador o abrir la Caja una vez. |
| Sin nombre de negocio al probar | ApiCelular no encuentra el config.ini real: está en otra carpeta. |
| Secreto ilegible | `C:\SistemaDual\api_celular\secreto.json` está dañado. |
| "La PC está ocupada" | Demasiados pedidos a la vez: esperar unos segundos. |
| No conecta con Tailscale en verde | Abrir `http://<IP de Tailscale de la PC>:8766/api/salud` en el navegador del celular: si no carga, la PC está dormida o la API caída (ver sección 6). |

## 9. Seguridad

- **Regla 1:** el 8766 nunca se abre a internet ni con port forwarding. Además del firewall (regla
  `OtterApiCelular-Tailscale`, atada a la placa de Tailscale), la propia API rechaza todo lo que no
  llegue por Tailscale, y desde la misma PC solo contesta `/api/salud`.
- El **PIN no se guarda en el celular**: lo valida la PC, que devuelve una sesión firmada de 30 días
  guardada en el llavero cifrado de Android. En la PC se guarda con sal y derivación lenta.
- **5 PIN incorrectos** desde un mismo aparato lo bloquean 5 minutos; más de 20 en 5 minutos sumando
  todos bloquean el ingreso para todos durante 5 minutos.
- Cambiar el PIN o correr `cerrar-sesiones` cierra la sesión de todos los celulares.
- La app se **bloquea sola** después de 2 minutos en segundo plano (huella o PIN).
- Las facturas PDF pueden pesar **hasta 20 MB**.
- **Regla 4:** el token del bot de Telegram nunca viaja al celular, y Ajustes muestra la IP de la PC
  tapada. Una captura de pantalla es un chat.

## 10. Firma del APK (para actualizar sin desinstalar)

Android solo deja instalar una versión nueva **encima** de la anterior si las dos están firmadas con
la misma clave. Sin clave propia, el CI firma con una clave descartable y para actualizar hay que
desinstalar (y volver a ingresar el PIN).

Para usar una clave fija, una sola vez:

```bash
keytool -genkey -v -keystore panel-dueno.jks -keyalg RSA -keysize 2048 -validity 10000 -alias panel-dueno
```

Pasarla a base64 (PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("panel-dueno.jks"))`)
y cargar en GitHub → *Settings → Secrets and variables → Actions* estos 4 secrets:

| Secret | Valor |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | el texto base64 del `.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | la contraseña del keystore |
| `ANDROID_KEY_ALIAS` | `panel-dueno` |
| `ANDROID_KEY_PASSWORD` | la contraseña de la clave |

**Guardá el `.jks` y las contraseñas en un lugar seguro**: si se pierden, no se puede actualizar la
app instalada sin desinstalarla. Para publicar una versión descargable desde *Releases*: subir
`version:` en `pubspec.yaml` y pushear un tag `movil-v<versión>`.

## 11. Desarrollo

```bash
cd apps/movil_dueno
flutter pub get
flutter analyze
flutter test                                   # incluye contrato_test.dart (el fixture del contrato)
flutter test test/sesion_test.dart --plain-name "bloquea"   # uno solo
```

El contrato con la API vive en `test/fixtures/contrato_api_celular_v2.json`: lo compara contra la API
real `tests/test_api_celular_contrato.py` (Python) y lo parsea `test/contrato_test.dart`. Si cambia un
endpoint, se cambian los dos lados y `test/servidor_falso.dart`.

API en desarrollo (desde la raíz del repo; **nunca** `consola` en la PC del local):

```bash
python services/api_celular_servicio.py autoprueba
python services/api_celular_servicio.py definir-pin --base <carpeta de prueba>
python services/api_celular_servicio.py consola --base <carpeta de prueba>
```

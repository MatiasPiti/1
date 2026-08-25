# =====================================================================
# Crea accesos directos en el Escritorio para las apps ya instaladas.
# Uso (PowerShell, desde cualquier carpeta):
#   powershell -ExecutionPolicy Bypass -File build\crear_accesos_directos.ps1
#
# Por defecto busca las apps en C:\SistemaDual\. Si instalaste en otro
# lado, pasá la ruta:
#   powershell -ExecutionPolicy Bypass -File build\crear_accesos_directos.ps1 -Raiz "D:\Otra\Ruta"
# =====================================================================
param(
    [string]$Raiz = "C:\SistemaDual"
)

$Escritorio = [Environment]::GetFolderPath("Desktop")
$Shell = New-Object -ComObject WScript.Shell

# Nombre visible => ruta relativa a $Raiz del .exe
$Apps = @{
    "Caja"           = "MaestroCaja\MaestroCaja.exe"
    "Panel del Dueno" = "MaestroDueno\MaestroDueno.exe"
}

foreach ($nombre in $Apps.Keys) {
    $rutaExe = Join-Path $Raiz $Apps[$nombre]
    if (-not (Test-Path $rutaExe)) {
        Write-Warning "No se encontro $rutaExe (revisa que ya lo hayas instalado ahi). Se omite."
        continue
    }
    $rutaAcceso = Join-Path $Escritorio "$nombre.lnk"
    $Acceso = $Shell.CreateShortcut($rutaAcceso)
    $Acceso.TargetPath = $rutaExe
    $Acceso.WorkingDirectory = Split-Path $rutaExe
    $Acceso.IconLocation = $rutaExe
    $Acceso.Description = $nombre
    $Acceso.Save()
    Write-Host "Creado: $rutaAcceso"
}

Write-Host "`nListo. Revisa el Escritorio."

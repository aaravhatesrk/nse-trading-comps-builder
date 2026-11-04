param([string]$Path, [string]$CellsJson)
$ErrorActionPreference = 'Stop'
$cells = $CellsJson | ConvertFrom-Json
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
$wb = $null
try {
    $wb = $xl.Workbooks.Open($Path)
    $xl.CalculateFull()
    $out = [ordered]@{}
    foreach ($c in $cells) {
        $out["$($c.sheet)!$($c.addr)"] = $wb.Worksheets.Item($c.sheet).Range($c.addr).Value2
    }
    $out | ConvertTo-Json -Compress
}
finally {
    if ($wb) { $wb.Close($false) }
    $xl.Quit()
}

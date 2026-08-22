powershell
# .NET bileşenlerini yükle
Add-Type -AssemblyName System.Windows.Forms

# Konsol ayarlarını yap
$console = [Console]::Out
$console.BackgroundColor = "Black"
Clear-Host

# Ekran boyutlarını al
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$width = $screen.Width / 8  # Her karakter 8 piksel genişliğinde
$height = $screen.Height

# Karakter seti ve renkler
$chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.<>?/~'
$colors = 'Red', 'Green', 'Yellow', 'Cyan', 'Magenta', 'White'

# Animasyon sistemi
$columns = @()
for ($x=0; $x -lt $width; $x++) {
    $columns += @{
        X = $x
        Y = Get-Random -Minimum 0 -Maximum $height
        Char = Get-Random -InputObject $chars.ToCharArray()
        Color = Get-Random -InputObject $colors
        Speed = Get-Random -Minimum 10 -Maximum 50
    }
}

# Ana döngü
try {
    while ($true) {
        # Ekranı temizle
        $console.Clear()
        
        # Her sütunu güncelle
        foreach ($col in $columns) {
            # Karakteri çiz
            $console.ForegroundColor = $col.Color
            $console.SetCursorPosition($col.X, $col.Y)
            $console.Write($col.Char)
            
            # Pozisyonu güncelle
            $col.Y += $col.Speed
            if ($col.Y -ge $height) {
                $col.Y = 0
                $col.Char = Get-Random -InputObject $chars.ToCharArray()
                $col.Color = Get-Random -InputObject $colors
                $col.Speed = Get-Random -Minimum 10 -Maximum 50
            }
        }
        
        Start-Sleep -Milliseconds 50
    }
}
catch {
    # Hata durumunda temizleme
    $console.ResetColor()
    Clear-Host
}